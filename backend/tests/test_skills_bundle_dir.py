"""GitHub #5：Skills bundle 默认落到 static/skills，避开 lwa **/dist ignore。"""
from __future__ import annotations

from pathlib import Path

import app.services.skill_catalog as skill_catalog
from app.services.skill_catalog import resolve_bundle_dir


def test_repo_layout_prefers_backend_static_skills(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    (repo / "backend" / "app").mkdir(parents=True)
    monkeypatch.delenv("SKILLS_BUNDLE_DIR", raising=False)
    chosen = resolve_bundle_dir(project_root=repo)
    assert chosen == repo / "backend" / "static" / "skills"


def test_docker_layout_prefers_app_static_skills(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "app"
    (root / "skills").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("", encoding="utf-8")
    monkeypatch.delenv("SKILLS_BUNDLE_DIR", raising=False)
    chosen = resolve_bundle_dir(project_root=root)
    assert chosen == root / "static" / "skills"


def test_legacy_dist_used_when_already_built(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    (repo / "backend" / "app").mkdir(parents=True)
    legacy = repo / "dist" / "skills"
    legacy.mkdir(parents=True)
    (legacy / "manifest.json").write_text('{"version":"0.2.5"}', encoding="utf-8")
    monkeypatch.delenv("SKILLS_BUNDLE_DIR", raising=False)
    chosen = resolve_bundle_dir(project_root=repo)
    assert chosen == legacy


def test_env_overrides(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "custom"
    monkeypatch.setenv("SKILLS_BUNDLE_DIR", str(custom))
    chosen = resolve_bundle_dir(project_root=tmp_path)
    assert chosen == custom


def test_default_root_survives_missing_skills_dir(tmp_path: Path, monkeypatch) -> None:
    """lwa 容器布局：只有 app/ 与 static/skills/，无 skills/ 源目录。

    bundle 根解析不得依赖 skills/ 存在，否则根退化到 /、找不到随 backend/ 携带的预构建 bundle。
    """
    app_root = tmp_path / "current"
    (app_root / "app").mkdir(parents=True)
    bundle = app_root / "static" / "skills"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text('{"version":"0.2.5"}', encoding="utf-8")
    monkeypatch.setattr(skill_catalog, "_APP_ROOT", app_root)
    monkeypatch.delenv("SKILLS_BUNDLE_DIR", raising=False)
    assert skill_catalog.resolve_bundle_dir() == bundle


def test_default_root_repo_layout(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "backend" / "app").mkdir(parents=True)
    monkeypatch.setattr(skill_catalog, "_APP_ROOT", repo / "backend")
    monkeypatch.delenv("SKILLS_BUNDLE_DIR", raising=False)
    assert skill_catalog.resolve_bundle_dir() == repo / "backend" / "static" / "skills"


def test_ensure_returns_prebuilt_bundle_in_backend_only_layout(tmp_path: Path, monkeypatch) -> None:
    """lwa：bundle 已随 backend/ 携带时，启动兜底直接命中，不需要 skills/ 与 scripts/。"""
    app_root = tmp_path / "current"
    (app_root / "app").mkdir(parents=True)
    bundle = app_root / "static" / "skills"
    bundle.mkdir(parents=True)
    (bundle / "skills-0.2.5.zip").write_bytes(b"zip")
    monkeypatch.setattr(skill_catalog, "_APP_ROOT", app_root)
    # ensure 会重绑模块级 BUNDLE_DIR，先登记原值让 monkeypatch 在收尾时恢复
    monkeypatch.setattr(skill_catalog, "BUNDLE_DIR", skill_catalog.BUNDLE_DIR)
    monkeypatch.delenv("SKILLS_BUNDLE_DIR", raising=False)
    assert skill_catalog.ensure_skills_bundle() == bundle


def test_ensure_builds_bundle_when_sources_available(tmp_path: Path, monkeypatch) -> None:
    """官方镜像 / 本机布局：bundle 缺失但 scripts/ 与 skills/ 可达时，启动兜底现场构建。

    脚本与 skills 源取自真实仓库根（模块级 _PROJECT_ROOT），输出落到 _APP_ROOT 的 static/skills。
    """
    repo = tmp_path / "repo"
    (repo / "backend" / "app").mkdir(parents=True)
    monkeypatch.setattr(skill_catalog, "_APP_ROOT", repo / "backend")
    monkeypatch.setattr(skill_catalog, "BUNDLE_DIR", skill_catalog.BUNDLE_DIR)
    monkeypatch.delenv("SKILLS_BUNDLE_DIR", raising=False)
    result = skill_catalog.ensure_skills_bundle()
    assert result == repo / "backend" / "static" / "skills"
    assert (repo / "backend" / "static" / "skills" / "manifest.json").is_file()
    assert any((repo / "backend" / "static" / "skills").glob("skills-*.zip"))
