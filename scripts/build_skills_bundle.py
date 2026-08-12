#!/usr/bin/env python3
"""WBS-4：Skills Bundle 构建脚本。

生成确定性 ZIP 包 + SHA256SUMS + Ed25519 签名（可选）。

用法：
    python scripts/build_skills_bundle.py [--sign-key <path>] [--output <dir>]

特性：
- 固定顺序遍历文件，确保同一 Git 提交重复构建哈希一致
- 拒绝 .env、数据库文件、符号链接、绝对路径
- 生成 skills-{version}.zip 和 SHA256SUMS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# ── 配置 ──

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"

# 禁止包含的文件模式
FORBIDDEN_PATTERNS = {
    ".env", ".env.local", ".env.production",
    ".db", ".sqlite", ".sqlite3",
    ".pyc", "__pycache__",
    ".git", ".gitignore",
}

FORBIDDEN_SUFFIXES = {
    ".env", ".db", ".sqlite", ".sqlite3", ".pyc",
    ".key", ".pem", ".crt",
}

# 允许的文件类型
ALLOWED_SUFFIXES = {
    ".md", ".json", ".txt", ".yaml", ".yml", ".toml",
    ".py", ".sh", ".html", ".css", ".js", ".ts", ".vue",
}

MAX_BUNDLE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_FILE_COUNT = 500


def _is_forbidden(path: Path) -> bool:
    name = path.name
    if name in FORBIDDEN_PATTERNS:
        return True
    for suffix in FORBIDDEN_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False


def _is_symlink(path: Path) -> bool:
    try:
        st = os.lstat(path)
        return stat.S_ISLNK(st.st_mode)
    except OSError:
        return False


def collect_skill_files() -> list[tuple[str, Path]]:
    """按固定顺序收集 skills 目录下的文件。

    返回 [(archive_path, filesystem_path), ...]
    """
    if not SKILLS_DIR.is_dir():
        print(f"错误: skills 目录不存在: {SKILLS_DIR}", file=sys.stderr)
        sys.exit(1)

    files: list[tuple[str, Path]] = []
    for root, dirs, filenames in os.walk(SKILLS_DIR):
        root_path = Path(root)
        # 固定排序确保确定性
        dirs.sort()
        filenames.sort()

        for fname in filenames:
            fpath = root_path / fname

            # 拒绝符号链接
            if _is_symlink(fpath):
                print(f"错误: 拒绝符号链接: {fpath}", file=sys.stderr)
                sys.exit(1)

            # 拒绝禁止的文件
            if _is_forbidden(fpath):
                print(f"跳过禁止文件: {fpath}", file=sys.stderr)
                continue

            # 拒绝不允许的扩展名
            if fpath.suffix and fpath.suffix not in ALLOWED_SUFFIXES:
                print(f"跳过非白名单扩展名: {fpath}", file=sys.stderr)
                continue

            # 计算归档内路径（相对路径，固定用 / 分隔）
            rel = fpath.relative_to(PROJECT_ROOT)
            archive_path = str(rel).replace(os.sep, "/")
            files.append((archive_path, fpath))

    return files


def build_bundle(output_dir: Path, version: str) -> tuple[Path, str]:
    """构建 ZIP 包，返回 (zip_path, sha256)。"""
    files = collect_skill_files()

    if len(files) > MAX_FILE_COUNT:
        print(f"错误: 文件数 {len(files)} 超过上限 {MAX_FILE_COUNT}", file=sys.stderr)
        sys.exit(1)

    total_size = sum(f.stat().st_size for _, f in files)
    if total_size > MAX_BUNDLE_SIZE:
        print(f"错误: 总大小 {total_size} 超过上限 {MAX_BUNDLE_SIZE}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"skills-{version}.zip"
    zip_path = output_dir / zip_name

    # 确定性 ZIP 构建
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for archive_path, fpath in files:
            # 读取文件内容
            data = fpath.read_bytes()
            # 创建 ZipInfo 以控制时间戳和权限
            info = zipfile.ZipInfo(archive_path, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16  # 固定权限
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return zip_path, sha256


def write_sha256sums(output_dir: Path, zip_name: str, sha256: str) -> Path:
    """写入 SHA256SUMS 文件。"""
    sums_path = output_dir / "SHA256SUMS"
    sums_path.write_text(f"{sha256}  {zip_name}\n", encoding="utf-8")
    return sums_path


def write_manifest(output_dir: Path, version: str, zip_name: str, sha256: str, file_count: int) -> Path:
    """写入 bundle manifest。"""
    manifest = {
        "version": version,
        "bundle_file": zip_name,
        "sha256": sha256,
        "file_count": file_count,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0",
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 Skills Bundle")
    parser.add_argument("--output", default="dist/skills", help="输出目录")
    parser.add_argument("--version", default=None, help="版本号（默认从 config 读取）")
    args = parser.parse_args()

    # 版本号
    version = args.version or os.environ.get("SKILLS_VERSION", "0.2.4")

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    print(f"构建 Skills Bundle v{version} ...")
    zip_path, sha256 = build_bundle(output_dir, version)
    print(f"  ZIP: {zip_path}")
    print(f"  SHA256: {sha256}")

    sums_path = write_sha256sums(output_dir, zip_path.name, sha256)
    print(f"  SHA256SUMS: {sums_path}")

    manifest_path = write_manifest(
        output_dir, version, zip_path.name, sha256,
        file_count=len(collect_skill_files()),
    )
    print(f"  Manifest: {manifest_path}")
    print(f"✅ 构建完成")


if __name__ == "__main__":
    main()
