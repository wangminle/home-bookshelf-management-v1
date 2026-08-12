"""BUG-158/CHK-048：Skills Docker 分发版本一致性测试。

验证发现索引声明的 bundle_version 与 dist/skills/ 中实际产物文件名一致，
确保 /agent/skills/download/{version}.zip 不会 404。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import agent_discovery, skill_catalog


@pytest.fixture
def bundle_dir() -> Path:
    return skill_catalog.BUNDLE_DIR


def test_get_skills_bundle_version_reads_from_manifest(bundle_dir: Path) -> None:
    """_get_skills_bundle_version 必须与 manifest.json 中的 version 一致。"""
    version = agent_discovery._get_skills_bundle_version()

    manifest_path = bundle_dir / "manifest.json"
    if manifest_path.is_file():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert version == data["version"], (
            f"_get_skills_bundle_version 返回 {version!r}，"
            f"但 manifest.json 声明 {data['version']!r}"
        )
    else:
        # manifest 不存在时回退到 _SKILLS_VERSION
        assert version == skill_catalog._SKILLS_VERSION


def test_bundle_version_not_hardcoded_date() -> None:
    """版本号不得是硬编码的日期格式（BUG-158 根因）。"""
    version = agent_discovery._get_skills_bundle_version()
    # 旧 bug 值
    assert version != "2026.08.11.1", "版本仍为硬编码的日期格式"
    # 应该是语义版本
    assert "." in version


def test_declared_version_matches_actual_zip(bundle_dir: Path) -> None:
    """发现索引声明的版本必须有对应的 ZIP 文件。"""
    version = agent_discovery._get_skills_bundle_version()
    expected_zip = bundle_dir / f"skills-{version}.zip"
    assert expected_zip.is_file(), (
        f"发现索引声明 bundle_version={version}，"
        f"但 {expected_zip} 不存在"
    )


def test_skills_index_archive_url_consistent() -> None:
    """build_skills_index 生成的 archive_url 必须指向存在的版本。"""
    index = agent_discovery.build_skills_index()
    version = index.bundle_version

    for entry in index.skills:
        assert entry.archive_url == f"/agent/skills/download/{version}.zip", (
            f"{entry.name} 的 archive_url 与 bundle_version 不一致"
        )

    # archive_url 指向的 ZIP 必须实际存在
    bundle_path = skill_catalog.BUNDLE_DIR / f"skills-{version}.zip"
    assert bundle_path.is_file(), (
        f"archive_url 指向 skills-{version}.zip，但文件不存在"
    )


def test_skills_index_has_sha256() -> None:
    """每个 skill 条目必须携带非空 SHA256（bundle 存在时）。"""
    version = agent_discovery._get_skills_bundle_version()
    bundle_path = skill_catalog.BUNDLE_DIR / f"skills-{version}.zip"

    if bundle_path.is_file():
        index = agent_discovery.build_skills_index()
        for entry in index.skills:
            assert entry.sha256, f"{entry.name} 缺少 SHA256"
            assert len(entry.sha256) == 64, f"{entry.name} 的 SHA256 长度异常"


def test_serve_bundle_returns_file_for_declared_version() -> None:
    """serve_bundle 必须为声明版本返回 FileResponse。"""
    version = agent_discovery._get_skills_bundle_version()
    bundle_path = skill_catalog.BUNDLE_DIR / f"skills-{version}.zip"

    if bundle_path.is_file():
        resp = skill_catalog.serve_bundle(version)
        assert "skills-" in str(resp.path)
        assert version in resp.filename


def test_manifest_version_matches_zip_filename(bundle_dir: Path) -> None:
    """manifest.json 的 version 必须与 ZIP 文件名中的版本一致。"""
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("dist/skills/manifest.json 不存在（未构建 bundle）")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = data["version"]
    bundle_file = data["bundle_file"]

    assert bundle_file == f"skills-{version}.zip", (
        f"manifest.json: version={version}, bundle_file={bundle_file}，不匹配"
    )


def test_http_skills_index_has_download_metadata(client: TestClient) -> None:
    """HTTP /agent/skills/index.json 必须返回 bundle_version 和 archive_url。

    BUG-158 回归测试：该端点曾错误调用 skill_catalog.build_skills_index()（简单
    字典，缺少 archive_url），导致 Agent 无法构造下载 URL。
    """
    r = client.get("/agent/skills/index.json")
    assert r.status_code == 200
    data = r.json()

    # 必须有 bundle_version 字段（简单字典版本没有这个字段）
    assert "bundle_version" in data, "索引缺少 bundle_version 字段"
    bundle_ver = data["bundle_version"]

    # 每个 skill 条目必须有 archive_url
    assert len(data["skills"]) > 0, "索引没有任何 skill 条目"
    for entry in data["skills"]:
        assert "archive_url" in entry, f"{entry.get('name')} 缺少 archive_url"
        expected_url = f"/agent/skills/download/{bundle_ver}.zip"
        assert entry["archive_url"] == expected_url, (
            f"{entry['name']} archive_url={entry['archive_url']!r}，期望 {expected_url!r}"
        )


def test_http_skills_download_end_to_end(client: TestClient) -> None:
    """HTTP 端到端：从索引获取版本 → 下载 ZIP → 验证内容。"""
    # 1. 从索引获取版本和下载 URL
    r = client.get("/agent/skills/index.json")
    assert r.status_code == 200
    data = r.json()
    bundle_ver = data["bundle_version"]

    # 2. 下载 ZIP
    r = client.get(f"/agent/skills/download/{bundle_ver}.zip")
    assert r.status_code == 200, f"下载 {bundle_ver} 失败: {r.status_code} {r.text}"
    assert r.headers.get("content-length") is not None
    assert len(r.content) > 0

    # 3. 验证是合法 ZIP
    import io
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert len(names) > 0, "ZIP 为空"
    # 至少包含一个 SKILL.md
    assert any("SKILL.md" in n for n in names), "ZIP 不含 SKILL.md"

    # 4. 过期版本 → 404
    r = client.get("/agent/skills/download/2026.08.11.1.zip")
    assert r.status_code == 404
