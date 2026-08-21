"""DEV-025 / GitHub #2：public-health 暴露前端构建版本。"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.agent_discovery import build_public_health, read_frontend_build_info


def test_read_frontend_build_info_missing(tmp_path: Path) -> None:
    assert read_frontend_build_info(tmp_path) == (None, None)


def test_read_frontend_build_info_from_version_json(tmp_path: Path) -> None:
    payload = {"frontend_version": "0.3.5", "build_time": "2026-08-20T04:00:00Z"}
    (tmp_path / "version.json").write_text(json.dumps(payload), encoding="utf-8")
    assert read_frontend_build_info(tmp_path) == ("0.3.5", "2026-08-20T04:00:00Z")


def test_public_health_includes_frontend_version_fields() -> None:
    data = build_public_health()
    dumped = data.model_dump()
    assert "frontend_version" in dumped
    assert "build_time" in dumped
    assert "app_version" in dumped
    assert dumped["app_version"] == "0.3.9"
