from __future__ import annotations

from datetime import datetime, timezone


def utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def local_today_iso() -> str:
    """与 reading_logs.log_date 同源：用户本地日历日（家庭服务器本地时区）。"""
    return datetime.now().date().isoformat()
