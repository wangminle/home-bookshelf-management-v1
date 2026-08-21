"""DEV-025 / GitHub #2：doctor 在 static 漂移时告警。"""
from __future__ import annotations

from unittest.mock import MagicMock

from bookshelf.doctor import run_doctor


def _client(health_data: dict) -> MagicMock:
    client = MagicMock()
    client.base_url = "http://127.0.0.1:8000"
    client.health.return_value = {
        "ok": True,
        "_http_status": 200,
        "data": {**health_data, "auth_protected": True, "database": "unknown"},
    }
    client.members.side_effect = RuntimeError("[HTTP 401] unauthorized")
    return client


def test_doctor_warns_when_frontend_version_missing() -> None:
    report = run_doctor(
        _client(
            {
                "status": "available",
                "service": "home-bookshelf",
                "app_version": "0.3.5",
            }
        )
    )
    assert any("static" in w and "version" in w for w in report.warnings)


def test_doctor_warns_when_frontend_lags_app() -> None:
    report = run_doctor(
        _client(
            {
                "status": "available",
                "service": "home-bookshelf",
                "app_version": "0.3.5",
                "frontend_version": "0.3.0",
                "build_time": "2026-08-10T00:00:00Z",
            }
        )
    )
    assert any("落后于代码版本" in w for w in report.warnings)


def test_doctor_silent_when_versions_match() -> None:
    report = run_doctor(
        _client(
            {
                "status": "available",
                "service": "home-bookshelf",
                "app_version": "0.3.5",
                "frontend_version": "0.3.5",
                "build_time": "2026-08-20T00:00:00Z",
            }
        )
    )
    assert not any("落后于代码版本" in w for w in report.warnings)
    assert not any("static" in w and "version" in w for w in report.warnings)
