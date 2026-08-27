"""CHK-087 修复回归：BUG-225/226/227。

- BUG-225：GET /books 状态筛选——非 Owner 强制 ctx.member_id（query member_id 被忽略；
  无参时不按全家庭聚合而是按本人聚合）。
- BUG-227：catalog_visibility 仅 Owner 下发（Member/Agent 列表与详情均不含）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, Member, ReadingProgress
from app.services import security_audit


def _make_client(db_session: Session) -> TestClient:
    def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    owner_id = client.get("/auth/session").json()["member_id"]
    r = client.post("/api/v1/members", json={"name": "甲", "role": "member"})
    mid = r.json()["data"]["id"]

    from app.services import agent_access
    agent_access.set_member_password(db_session, db_session.get(Member, mid), "m-pass-123456")

    c = _make_client(db_session)
    assert c.post("/auth/login", json={"username": "甲", "password": "m-pass-123456"}).status_code == 200

    # 造书：Owner 在读、Member 已读完
    book_a = Book(title="BUG225-Owner在读")
    book_b = Book(title="BUG225-Member读完")
    db_session.add_all([book_a, book_b])
    db_session.commit()
    db_session.add(ReadingProgress(book_id=book_a.id, member_id=owner_id, status="reading"))
    db_session.add(ReadingProgress(book_id=book_b.id, member_id=mid, status="finished"))
    db_session.commit()

    # 设置可见级别（用于 BUG-227 测试）
    from app.api.v1.books import _require_owner  # noqa: F401
    client.patch(f"/api/v1/books/{book_a.id}/visibility", json={"visibility": "public"})
    client.patch(f"/api/v1/books/{book_b.id}/visibility", json={"visibility": "private"})

    return {"owner_id": owner_id, "mid": mid, "member_client": c,
            "book_a": book_a.id, "book_b": book_b.id}


# ── BUG-225：状态筛选服务端强制归属 ──


def test_member_status_filter_returns_own_only(world: dict) -> None:
    """Member 状态筛选只能看到自己的进度（无参时不按全家庭聚合）。"""
    c = world["member_client"]
    # Member 查 finished → 只有自己读完的书
    r = c.get("/api/v1/books?status=finished")
    assert r.status_code == 200
    titles = [b["title"] for b in r.json()["data"]["items"]]
    assert "BUG225-Member读完" in titles
    assert "BUG225-Owner在读" not in titles

    # Member 查 reading → 空列表（自己没有 reading）
    r = c.get("/api/v1/books?status=reading")
    titles = [b["title"] for b in r.json()["data"]["items"]]
    assert "BUG225-Owner在读" not in titles  # 不能看到 Owner 在读的书


def test_member_cannot_spoof_query_member_id(world: dict) -> None:
    """Member 传 query member_id=<Owner> 也被忽略，强制用 ctx.member_id。"""
    c = world["member_client"]
    r = c.get(f"/api/v1/books?status=reading&member_id={world['owner_id']}")
    assert r.status_code == 200
    titles = [b["title"] for b in r.json()["data"]["items"]]
    assert "BUG225-Owner在读" not in titles, "query member_id 伪造应被忽略"


def test_owner_status_filter_sees_all(world: dict, client: TestClient) -> None:
    """Owner 仍能看到全家庭聚合（无 member_id 时）或指定成员（带 member_id）。"""
    r = client.get("/api/v1/books?status=reading")
    titles = [b["title"] for b in r.json()["data"]["items"]]
    assert "BUG225-Owner在读" in titles  # Owner 可以看到 Owner 在读的

    r = client.get(f"/api/v1/books?status=finished&member_id={world['mid']}")
    titles = [b["title"] for b in r.json()["data"]["items"]]
    assert "BUG225-Member读完" in titles  # Owner 可以代查 Member


# ── BUG-227：catalog_visibility 仅 Owner 下发 ──


def test_member_detail_no_catalog_visibility(world: dict) -> None:
    """Member 详情不含 catalog_visibility（None）。"""
    c = world["member_client"]
    r = c.get(f"/api/v1/books/{world['book_a']}")
    assert r.status_code == 200
    assert r.json()["data"]["catalog_visibility"] is None


def test_owner_detail_has_catalog_visibility(world: dict, client: TestClient) -> None:
    """Owner 详情返回实际 catalog_visibility 值。"""
    r = client.get(f"/api/v1/books/{world['book_a']}")
    assert r.json()["data"]["catalog_visibility"] == "public"
    r = client.get(f"/api/v1/books/{world['book_b']}")
    assert r.json()["data"]["catalog_visibility"] == "private"


def test_member_list_no_catalog_visibility(world: dict) -> None:
    """Member 列表所有书的 catalog_visibility 均为 None。"""
    c = world["member_client"]
    r = c.get("/api/v1/books?limit=100")
    for b in r.json()["data"]["items"]:
        assert b["catalog_visibility"] is None
