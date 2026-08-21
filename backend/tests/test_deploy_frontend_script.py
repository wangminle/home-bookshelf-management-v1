"""DEV-026 / GitHub #3：一键前端构建脚本契约。"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_frontend.sh"


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()
    text = SCRIPT.read_text(encoding="utf-8")
    assert "rsync" in text
    assert "--delete" in text
    assert "skills/" in text
    assert "VITE_BASE" in text


def test_dry_run_direct_deploy_forces_root_base() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "VITE_BASE=/" in result.stdout
    assert "rsync -a --delete" in result.stdout
    assert "--exclude" in result.stdout


def test_dry_run_alias_sets_vite_base() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", "--base", "/home-bookshelf/"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "VITE_BASE=/home-bookshelf/" in result.stdout


def test_dry_run_builds_skills_bundle() -> None:
    """GitHub #5：bundle 必须由部署脚本产出到 backend/static/skills/，随 lwa import 携带。"""
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "build_skills_bundle.py" in result.stdout
    assert "backend/static/skills" in result.stdout
