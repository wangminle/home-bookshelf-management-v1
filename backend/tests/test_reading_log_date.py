"""TST-001: 阅读日志日期与边界——log_date 必填/合法/未来拒绝/空白裁剪，pages/session 校验。

schema 校验用纯 Pydantic，端点用 client（需 book 存在）。
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.reading_log import ReadingLogCreate
from app.utils.time_helpers import local_today_iso


def _today():
    return date.fromisoformat(local_today_iso())


def _tomorrow():
    return _today() + timedelta(days=1)


def _yesterday():
    return _today() - timedelta(days=1)


# ---- 纯 schema 校验 ----


def test_log_date_valid_today_accepted():
    obj = ReadingLogCreate(log_date=_today().isoformat())
    assert obj.log_date == _today().isoformat()


def test_log_date_valid_yesterday_accepted():
    obj = ReadingLogCreate(log_date=_yesterday().isoformat())
    assert obj.log_date == _yesterday().isoformat()


def test_log_date_future_rejected():
    with pytest.raises(ValidationError):
        ReadingLogCreate(log_date=_tomorrow().isoformat())


def test_log_date_invalid_calendar_rejected():
    with pytest.raises(ValidationError):
        ReadingLogCreate(log_date="2026-13-01")


def test_log_date_required():
    with pytest.raises(ValidationError):
        ReadingLogCreate()


def test_log_date_strips_whitespace():
    obj = ReadingLogCreate(log_date=f"  {_today().isoformat()}  ")
    assert obj.log_date == _today().isoformat()


def test_log_date_slash_format_rejected():
    with pytest.raises(ValidationError):
        ReadingLogCreate(log_date="2026/06/15")


def test_pages_read_zero_ok():
    obj = ReadingLogCreate(log_date=_today().isoformat(), pages_read=0)
    assert obj.pages_read == 0


def test_pages_read_negative_rejected():
    with pytest.raises(ValidationError):
        ReadingLogCreate(log_date=_today().isoformat(), pages_read=-1)


def test_minutes_read_negative_rejected():
    with pytest.raises(ValidationError):
        ReadingLogCreate(log_date=_today().isoformat(), minutes_read=-5)


def test_session_end_before_start_rejected():
    with pytest.raises(ValidationError):
        ReadingLogCreate(
            log_date=_today().isoformat(),
            session_start="2026-08-09T10:00:00",
            session_end="2026-08-09T09:00:00",
        )


# ---- 端点集成 ----


def test_reading_log_created_via_api(client):
    book = client.post("/api/v1/books", json={"title": "日志测试书"})
    book_id = book.json()["data"]["id"]
    r = client.post(
        f"/api/v1/books/{book_id}/reading-logs",
        json={"log_date": _today().isoformat(), "pages_read": 30},
    )
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["pages_read"] == 30
    assert d["log_date"] == _today().isoformat()


def test_reading_log_future_date_422_via_api(client):
    book = client.post("/api/v1/books", json={"title": "未来日志书"})
    book_id = book.json()["data"]["id"]
    r = client.post(
        f"/api/v1/books/{book_id}/reading-logs",
        json={"log_date": _tomorrow().isoformat()},
    )
    assert r.status_code == 422
