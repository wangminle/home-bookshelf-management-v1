"""GitHub #11：lwa 部署下 /agent/skills/index.json 的 skills 列表为空。

lwa 容器只导入 backend/，没有 skills/ 源目录，list_skills() 扫描 SKILLS_DIR
恒为空。回归：源码缺失时必须从 BUNDLE_DIR 的 bundle ZIP 解析技能清单，
使发现面 index.json 在纯 bundle 部署上仍可列出全部技能。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import app.services.skill_catalog as skill_catalog
from app.services import agent_discovery


def _bundle_skill_names() -> list[str]:
    """从真实 bundle ZIP 提取技能名（测试基准）。"""
    zip_path = skill_catalog.BUNDLE_DIR / f"skills-{skill_catalog._SKILLS_VERSION}.zip"
    if not zip_path.is_file():
        zips = sorted(skill_catalog.BUNDLE_DIR.glob("skills-*.zip"))
        if not zips:
            return []
        zip_path = zips[-1]
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(
            parts[-2]
            for entry in zf.namelist()
            if (parts := entry.strip("/").split("/"))[-1] == "SKILL.md" and parts[-2] != "skills"
        )


def test_list_skills_falls_back_to_bundle_when_source_missing(tmp_path: Path, monkeypatch) -> None:
    """lwa 布局：SKILLS_DIR 不存在时，从 bundle ZIP 解析出全部技能。"""
    monkeypatch.setattr(skill_catalog, "SKILLS_DIR", tmp_path / "nonexistent")

    skills = skill_catalog.list_skills()

    expected = _bundle_skill_names()
    assert expected, "测试前提：真实 bundle ZIP 应包含 SKILL.md"
    assert [s.name for s in skills] == expected


def test_fallback_entries_have_frontmatter_metadata(tmp_path: Path, monkeypatch) -> None:
    """兜底条目必须带 frontmatter 元数据，不能是空壳。"""
    monkeypatch.setattr(skill_catalog, "SKILLS_DIR", tmp_path / "nonexistent")

    for skill in skill_catalog.list_skills():
        assert skill.description, f"{skill.name} 缺少 description"
        assert skill.version, f"{skill.name} 缺少 version"


def test_source_dir_takes_precedence_over_bundle(tmp_path: Path, monkeypatch) -> None:
    """源码目录可用时优先，行为不变（开发环境不回归）。"""
    source = tmp_path / "skills"
    fake = source / "fake-skill"
    fake.mkdir(parents=True)
    (fake / "SKILL.md").write_text(
        "---\ndescription: 测试技能\nscopes: [books:read]\n---\n正文",
        encoding="utf-8",
    )
    monkeypatch.setattr(skill_catalog, "SKILLS_DIR", source)

    skills = skill_catalog.list_skills()

    assert [s.name for s in skills] == ["fake-skill"]
    assert skills[0].scopes == ["books:read"]


def test_corrupt_bundle_fallback_returns_empty(tmp_path: Path, monkeypatch) -> None:
    """bundle 损坏时兜底安全返回空列表，不抛异常。"""
    bundle = tmp_path / "static" / "skills"
    bundle.mkdir(parents=True)
    (bundle / f"skills-{skill_catalog._SKILLS_VERSION}.zip").write_bytes(b"not a zip")
    monkeypatch.setattr(skill_catalog, "SKILLS_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(skill_catalog, "BUNDLE_DIR", bundle)

    assert skill_catalog.list_skills() == []


def test_build_skills_index_populated_in_lwa_layout(tmp_path: Path, monkeypatch) -> None:
    """发现面索引在 lwa 布局下必须非空，且 archive_url 指向真实 bundle。"""
    monkeypatch.setattr(skill_catalog, "SKILLS_DIR", tmp_path / "nonexistent")

    index = agent_discovery.build_skills_index()

    assert index.skills, "index.json 的 skills 列表为空（#11 复现）"
    expected_url = f"/agent/skills/download/{index.bundle_version}.zip"
    for entry in index.skills:
        assert entry.archive_url == expected_url
        assert entry.sha256, f"{entry.name} 缺少 SHA256"


def test_http_skills_index_nonempty_in_lwa_layout(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """HTTP 端到端：模拟 lwa 无源码目录，index.json 仍返回完整技能清单。"""
    monkeypatch.setattr(skill_catalog, "SKILLS_DIR", tmp_path / "nonexistent")

    r = client.get("/agent/skills/index.json")
    assert r.status_code == 200
    data = r.json()

    assert data["skills"], "HTTP index.json 的 skills 列表为空（#11 复现）"
    assert [s["name"] for s in data["skills"]] == _bundle_skill_names()
