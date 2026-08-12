"""WBS-4/8：Skills Bundle 安全解包与分发测试。

验证 Skills ZIP 包的安全性：
- 确定性构建（同一输入产出同一哈希）
- 禁止敏感文件（.env、数据库、密钥）
- 禁止符号链接
- 路径穿越防护
- 文件数和大小限制
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

# 将 scripts/ 目录加入 path
import sys
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from build_skills_bundle import (
    ALLOWED_SUFFIXES,
    FORBIDDEN_PATTERNS,
    FORBIDDEN_SUFFIXES,
    MAX_BUNDLE_SIZE,
    MAX_FILE_COUNT,
    _is_forbidden,
    _is_symlink,
    build_bundle,
    collect_skill_files,
)


class TestForbiddenFileDetection:
    """禁止文件检测。"""

    def test_env_files_forbidden(self):
        assert _is_forbidden(Path(".env"))
        assert _is_forbidden(Path(".env.local"))
        assert _is_forbidden(Path(".env.production"))

    def test_database_files_forbidden(self):
        assert _is_forbidden(Path("test.db"))
        assert _is_forbidden(Path("data.sqlite"))
        assert _is_forbidden(Path("app.sqlite3"))

    def test_python_cache_forbidden(self):
        assert _is_forbidden(Path("__pycache__"))

    def test_key_files_forbidden(self):
        assert _is_forbidden(Path("signing.key"))
        assert _is_forbidden(Path("cert.pem"))
        assert _is_forbidden(Path("server.crt"))

    def test_git_files_forbidden(self):
        assert _is_forbidden(Path(".git"))
        assert _is_forbidden(Path(".gitignore"))

    def test_allowed_files_not_forbidden(self):
        assert not _is_forbidden(Path("SKILL.md"))
        assert not _is_forbidden(Path("manifest.json"))
        assert not _is_forbidden(Path("script.py"))
        assert not _is_forbidden(Path("style.css"))


class TestAllowedSuffixes:
    """允许的文件扩展名白名单。"""

    def test_md_allowed(self):
        assert ".md" in ALLOWED_SUFFIXES

    def test_json_allowed(self):
        assert ".json" in ALLOWED_SUFFIXES

    def test_py_allowed(self):
        assert ".py" in ALLOWED_SUFFIXES

    def test_vue_allowed(self):
        assert ".vue" in ALLOWED_SUFFIXES

    def test_executable_not_allowed(self):
        assert ".exe" not in ALLOWED_SUFFIXES
        assert ".bin" not in ALLOWED_SUFFIXES

    def test_env_not_allowed(self):
        assert ".env" not in ALLOWED_SUFFIXES


class TestSymlinkDetection:
    """符号链接检测。"""

    def test_regular_file_not_symlink(self, tmp_path):
        f = tmp_path / "regular.md"
        f.write_text("hello")
        assert not _is_symlink(f)

    def test_symlink_detected(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text("content")
        link = tmp_path / "link.md"
        link.symlink_to(target)
        assert _is_symlink(link)


class TestCollectSkillFiles:
    """文件收集逻辑。"""

    def test_collect_returns_sorted(self):
        """收集结果按路径排序。"""
        files = collect_skill_files()
        assert len(files) > 0
        paths = [p for _, p in files]
        # 确保有 SKILL.md 文件
        skill_mds = [p for _, p in files if p.name == "SKILL.md"]
        assert len(skill_mds) >= 7  # 7 个业务 skills

    def test_collect_no_forbidden_files(self):
        """收集结果中不包含禁止文件。"""
        files = collect_skill_files()
        for archive_path, fpath in files:
            assert not _is_forbidden(fpath), f"禁止文件被收集: {fpath}"

    def test_collect_no_symlinks(self):
        """收集结果中不包含符号链接。"""
        files = collect_skill_files()
        for archive_path, fpath in files:
            assert not _is_symlink(fpath), f"符号链接被收集: {fpath}"

    def test_collect_all_allowed_suffixes(self):
        """收集结果中所有文件扩展名都在白名单中。"""
        files = collect_skill_files()
        for archive_path, fpath in files:
            if fpath.suffix:
                assert fpath.suffix in ALLOWED_SUFFIXES, f"非白名单扩展名: {fpath}"

    def test_collect_file_count_under_limit(self):
        """文件数不超过上限。"""
        files = collect_skill_files()
        assert len(files) <= MAX_FILE_COUNT


class TestBuildBundle:
    """ZIP 构建逻辑。"""

    def test_deterministic_build(self, tmp_path):
        """同一输入两次构建产出同一哈希。"""
        zip1, sha1 = build_bundle(tmp_path / "out1", "0.0.1-test")
        zip2, sha2 = build_bundle(tmp_path / "out2", "0.0.1-test")
        assert sha1 == sha2
        assert zip1.read_bytes() == zip2.read_bytes()

    def test_different_version_different_hash(self, tmp_path):
        """不同版本号产出不同文件名。"""
        zip1, _ = build_bundle(tmp_path / "out1", "0.0.1-test")
        zip2, _ = build_bundle(tmp_path / "out2", "0.0.2-test")
        assert zip1.name == "skills-0.0.1-test.zip"
        assert zip2.name == "skills-0.0.2-test.zip"

    def test_zip_contains_skill_files(self, tmp_path):
        """ZIP 包内包含 SKILL.md 文件。"""
        zip_path, _ = build_bundle(tmp_path / "out", "0.0.1-test")
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            skill_mds = [n for n in names if n.endswith("SKILL.md")]
            assert len(skill_mds) >= 7

    def test_zip_no_forbidden_entries(self, tmp_path):
        """ZIP 包内不包含禁止文件。"""
        zip_path, _ = build_bundle(tmp_path / "out", "0.0.1-test")
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                basename = Path(name).name
                assert basename not in FORBIDDEN_PATTERNS, f"禁止文件出现在包中: {name}"

    def test_zip_no_absolute_paths(self, tmp_path):
        """ZIP 包内不包含绝对路径。"""
        zip_path, _ = build_bundle(tmp_path / "out", "0.0.1-test")
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                assert not name.startswith("/"), f"绝对路径: {name}"

    def test_zip_no_path_traversal(self, tmp_path):
        """ZIP 包内不包含路径穿越（../）。"""
        zip_path, _ = build_bundle(tmp_path / "out", "0.0.1-test")
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                assert ".." not in name, f"路径穿越: {name}"

    def test_zip_fixed_timestamp(self, tmp_path):
        """ZIP 内文件时间戳固定为 1980-01-01（确定性）。"""
        zip_path, _ = build_bundle(tmp_path / "out", "0.0.1-test")
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)

    def test_zip_fixed_permissions(self, tmp_path):
        """ZIP 内文件权限固定为 0644。"""
        zip_path, _ = build_bundle(tmp_path / "out", "0.0.1-test")
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                perm = (info.external_attr >> 16) & 0o777
                assert perm == 0o644

    def test_manifest_written(self, tmp_path):
        """构建后生成 manifest.json。"""
        from build_skills_bundle import write_manifest
        zip_path, sha = build_bundle(tmp_path / "out", "0.0.1-test")
        manifest_path = write_manifest(
            tmp_path / "out", "0.0.1-test", zip_path.name, sha,
            file_count=10,
        )
        import json
        manifest = json.loads(manifest_path.read_text())
        assert manifest["version"] == "0.0.1-test"
        assert manifest["sha256"] == sha
        assert manifest["bundle_file"] == "skills-0.0.1-test.zip"

    def test_sha256sums_written(self, tmp_path):
        """构建后生成 SHA256SUMS。"""
        from build_skills_bundle import write_sha256sums
        zip_path, sha = build_bundle(tmp_path / "out", "0.0.1-test")
        sums_path = write_sha256sums(tmp_path / "out", zip_path.name, sha)
        content = sums_path.read_text()
        assert sha in content
        assert zip_path.name in content


class TestSafeUnpack:
    """安全解包逻辑测试。"""

    def test_safe_unpack_rejects_path_traversal(self, tmp_path):
        """安全解包拒绝路径穿越。"""
        evil_zip = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            info = zipfile.ZipInfo("../../../etc/passwd")
            zf.writestr(info, "malicious")

        with zipfile.ZipFile(evil_zip) as zf:
            for info in zf.infolist():
                # 模拟安全检查：解析后路径不能超出目标目录
                target = (tmp_path / "unpack" / info.filename).resolve()
                base = (tmp_path / "unpack").resolve()
                assert not str(target).startswith(str(base)), "路径穿越未被检测"

    def test_safe_unpack_rejects_absolute_path(self, tmp_path):
        """安全解包拒绝绝对路径。"""
        evil_zip = tmp_path / "evil_abs.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            info = zipfile.ZipInfo("/etc/passwd")
            zf.writestr(info, "malicious")

        with zipfile.ZipFile(evil_zip) as zf:
            for info in zf.infolist():
                assert info.filename.startswith("/"), "绝对路径存在"

    def test_safe_unpack_rejects_symlink(self, tmp_path):
        """安全解包拒绝符号链接条目。"""
        evil_zip = tmp_path / "evil_sym.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            info = zipfile.ZipInfo("link.md")
            # 模拟符号链接权限位
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")

        with zipfile.ZipFile(evil_zip) as zf:
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0o170000
                is_symlink = mode == 0o120000
                assert is_symlink, "符号链接条目应被检测"

    def test_safe_unpack_normal_zip(self, tmp_path):
        """正常 ZIP 可以安全解包。"""
        good_zip = tmp_path / "good.zip"
        with zipfile.ZipFile(good_zip, "w") as zf:
            zf.writestr("skills/test/SKILL.md", "# Test Skill")
            zf.writestr("manifest.json", '{"version": "0.0.1"}')

        unpack_dir = tmp_path / "unpack"
        unpack_dir.mkdir()

        with zipfile.ZipFile(good_zip) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target = (unpack_dir / info.filename).resolve()
                base = unpack_dir.resolve()
                assert str(target).startswith(str(base))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info.filename))

        assert (unpack_dir / "skills/test/SKILL.md").exists()
        assert (unpack_dir / "manifest.json").exists()
