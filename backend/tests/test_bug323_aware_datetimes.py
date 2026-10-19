"""BUG-323：认证层必须同时接受 naive（SQLite）与 tz-aware（PostgreSQL timestamptz）的库内时间。

PG 驱动返回 tz-aware datetime，而 agent_access 以 naive UTC 取"当前时间"，
原先直接比较会抛 `can't compare offset-naive and offset-aware datetimes`。
这里不依赖 PG：在会话内把库内时间属性设为 aware（autoflush 关闭，不落库），
等价于 PG 返回值，SQLite 下即可稳定复现；PG 实机另由 TEST_DATABASE_URL 全量回归覆盖。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentGrant, AgentToken, Member, MemberCredential
from app.services import agent_access
from tests.test_bug166_167_168_auth import _agent_token

UTC = timezone.utc


def _aware(delta: timedelta) -> datetime:
    return datetime.now(UTC) + delta


def test_utc_naive_helper() -> None:
    naive = datetime(2026, 10, 19, 1, 2, 3)
    assert agent_access._utc_naive(naive) is naive
    assert agent_access._utc_naive(None) is None
    plus8 = datetime(2026, 10, 19, 9, 2, 3, tzinfo=timezone(timedelta(hours=8)))
    assert agent_access._utc_naive(plus8) == naive  # 先转 UTC 再去 tz，而不是只丢弃 tzinfo


def test_web_session_accepts_aware_expiry(client: TestClient, db_session: Session) -> None:
    owner = db_session.scalars(select(Member).where(Member.role == "owner")).first()
    token, row = agent_access.create_web_session(db_session, owner.id)

    row.expires_at = _aware(timedelta(hours=1))
    assert agent_access.verify_web_session(db_session, token).id == owner.id

    row.expires_at = _aware(timedelta(seconds=-5))
    assert agent_access.verify_web_session(db_session, token) is None
    db_session.rollback()


def test_agent_token_accepts_aware_expiry(client: TestClient, db_session: Session) -> None:
    _, plaintext = _agent_token(client, ["books:read"])
    token_row = db_session.scalars(select(AgentToken)).first()
    grant = db_session.get(AgentGrant, token_row.grant_id)

    token_row.expires_at = _aware(timedelta(days=1))
    grant.expires_at = _aware(timedelta(days=1))
    assert agent_access.verify_token(db_session, plaintext) is not None

    token_row.expires_at = _aware(timedelta(seconds=-5))
    assert agent_access.verify_token(db_session, plaintext) is None

    token_row.expires_at = _aware(timedelta(days=1))
    grant.expires_at = _aware(timedelta(seconds=-5))
    assert agent_access.verify_token(db_session, plaintext) is None
    db_session.rollback()


def test_issue_token_rejects_aware_expired_grant(client: TestClient, db_session: Session) -> None:
    _agent_token(client, ["books:read"])
    grant = db_session.scalars(select(AgentGrant)).first()
    grant.expires_at = _aware(timedelta(seconds=-5))
    with pytest.raises(HTTPException) as exc:
        agent_access.issue_token(db_session, grant.id)
    assert exc.value.status_code == 400 and "过期" in exc.value.detail
    db_session.rollback()


def test_password_lockout_accepts_aware_locked_until(client: TestClient, db_session: Session) -> None:
    owner = db_session.scalars(select(Member).where(Member.role == "owner")).first()
    agent_access.set_member_password(db_session, owner, "Correct-Horse-1")
    cred = agent_access.get_member_credential(db_session, owner.id)

    cred.locked_until = _aware(timedelta(minutes=5))
    with pytest.raises(HTTPException) as exc:
        agent_access.verify_member_password(db_session, owner, "Correct-Horse-1")
    assert exc.value.status_code == 429

    cred.locked_until = _aware(timedelta(minutes=-5))  # 已过期的锁不再拦截
    assert agent_access.verify_member_password(db_session, owner, "Correct-Horse-1") is True
