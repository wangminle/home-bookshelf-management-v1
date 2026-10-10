"""BUG-318：doctor 未读取成员时输出未知（null），不得回落为 0/[] 误导空库判断。"""
from __future__ import annotations

from unittest.mock import MagicMock

from bookshelf.doctor import emit_doctor, run_doctor


def _client(members_error: str | None, items: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.base_url = "http://127.0.0.1:8000"
    client.health.return_value = {
        "ok": True,
        "_http_status": 200,
        "data": {"auth_protected": True, "database": "unknown"},
    }
    if members_error is not None:
        client.members.side_effect = RuntimeError(members_error)
    else:
        client.members.return_value = {"data": {"items": items or []}}
    return client


def test_members_unknown_when_unauthorized() -> None:
    report = run_doctor(_client("[HTTP 401] unauthorized"))
    assert report.members_total is None
    assert report.members_bound is None
    assert report.members is None
    payload = report.to_payload()["data"]
    assert payload["checks"]["members_total"] is None
    assert payload["checks"]["members_bound"] is None
    assert payload["members"] is None
    assert any("不得按空库处理" in w for w in report.warnings)


def test_members_unknown_when_forbidden() -> None:
    report = run_doctor(_client("[HTTP 403] forbidden"))
    assert report.members_total is None
    assert report.members is None
    assert any("不得按空库处理" in w for w in report.warnings)


def test_members_unknown_when_endpoint_missing() -> None:
    report = run_doctor(_client("[HTTP 404] not found"))
    assert report.members_total is None
    assert report.members is None


def test_members_unknown_when_network_error() -> None:
    report = run_doctor(_client("connection reset"))
    assert report.members_total is None
    assert report.members is None
    assert any("不得按空库处理" in w for w in report.warnings)


def test_empty_library_still_reports_zero() -> None:
    report = run_doctor(_client(None, items=[]))
    assert report.members_total == 0
    assert report.members_bound == 0
    assert report.members == []


def test_emit_doctor_shows_unknown_members(capsys) -> None:
    report = run_doctor(_client("[HTTP 401] unauthorized"))
    emit_doctor(report.to_payload(), as_json=False)
    out = capsys.readouterr().out
    assert "成员: 未知" in out
    assert "成员: 0 人" not in out
