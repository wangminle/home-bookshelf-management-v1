"""CHK-088 阶段 2 验收补充：Owner 代查阅读状态的审计闭环。

Owner 用 GET /books?status=...&member_id=<other> 代查时：
- 写入共享安全审计事件 owner.delegate_status_query；
- details 含 acting_for_member_id 与 operator_member_id。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Book, Member, ReadingProgress
from app.services import security_audit


def test_owner_proxy_status_query_audited(client: TestClient, db_session: Session) -> None:
    """Owner 代查他人状态 → 共享审计事件含 acting_for + operator。"""
    security_audit.reset()
    owner_id = client.get("/auth/session").json()["member_id"]

    # 造一个 member 和书
    r = client.post("/api/v1/members", json={"name": "审计成员", "role": "member"})
    mid = r.json()["data"]["id"]
    book = Book(title="代查审计书")
    db_session.add(book)
    db_session.commit()
    db_session.add(ReadingProgress(book_id=book.id, member_id=mid, status="reading"))
    db_session.commit()

    # Owner 代查 member 的 reading 状态
    r = client.get(f"/api/v1/books?status=reading&member_id={mid}")
    assert r.status_code == 200

    # 断言审计事件
    events = security_audit.list_security_events(
        db_session, event_type="owner.delegate_status_query"
    )
    assert events, "缺少 owner.delegate_status_query 审计事件"
    payload = json.loads(events[0].payload or "{}")
    details = payload.get("details", {})
    assert details.get("acting_for_member_id") == mid
    assert details.get("operator_member_id") == owner_id
    assert details.get("status_filter") == "reading"
    security_audit.reset()


def test_owner_own_status_query_not_audited(client: TestClient, db_session: Session) -> None:
    """Owner 查自己的状态 → 不产生代查审计（非代操作）。"""
    security_audit.reset()
    owner_id = client.get("/auth/session").json()["member_id"]
    r = client.get(f"/api/v1/books?status=reading&member_id={owner_id}")
    assert r.status_code == 200
    events = security_audit.list_security_events(
        db_session, event_type="owner.delegate_status_query"
    )
    assert not events, "查自己不应产生代查审计"
    security_audit.reset()


def test_owner_no_member_id_not_audited(client: TestClient, db_session: Session) -> None:
    """Owner 不带 member_id 的全家庭查询 → 不产生代查审计。"""
    security_audit.reset()
    r = client.get("/api/v1/books?status=reading")
    assert r.status_code == 200
    events = security_audit.list_security_events(
        db_session, event_type="owner.delegate_status_query"
    )
    assert not events
    security_audit.reset()
