"""MCP 第二期：REST/MCP 语义一致性测试（WBS-MCP-9 Task 9.2 自动化部分）。

验收口径（MCP 设计 §2.1/§17）：
- 同一 Agent Grant 在 REST 与 MCP 得出一致的允许/拒绝结果；
- MCP 允许字段/权限是 REST 授权结果的安全子集（MCP 只能更窄）；
- MCP 与 Public Catalog 使用独立限流 Profile，额度互不覆盖。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, PurchaseRecord, ReadingNote
from app.services import rate_limit, security_audit
from app.utils.book_helpers import serialize_json_list


@pytest.fixture(autouse=True)
def _reset_state():
    rate_limit.reset()
    security_audit.reset()
    yield
    rate_limit.reset()
    security_audit.reset()


@pytest.fixture()
def mcp_on(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_enabled", True)
    # BUG-212：游标密钥须 >= 32 字符（低熵配置启动即拒绝）
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "parity-cursor-secret-high-entropy-0123456789")
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "testserver")


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    owner_id = client.get("/auth/session").json()["member_id"]
    book = Book(
        title="Parity 书", authors=serialize_json_list(["作者"]), category="科幻",
        language="zh", cover_path="covers/parity.jpg",
    )
    db_session.add(book)
    db_session.commit()
    db_session.add(ReadingNote(book_id=book.id, member_id=owner_id, content_md="PARITY_L3_NOTE"))
    db_session.add(PurchaseRecord(book_id=book.id, buyer_member_id=owner_id, price=1.0, channel="PARITY_CHANNEL"))
    db_session.commit()

    tokens = {}
    for label, scopes, data_scope in (
        ("pilot", ["books:read"], "household_shared"),
        ("no_scope", ["stats:read"], "household_shared"),
        ("legacy", ["books:read"], None),
    ):
        r = client.post("/agent-access/clients", json={"display_name": f"Parity-{label}"})
        cid = r.json()["id"]
        payload = {"agent_client_id": cid, "member_id": owner_id, "scopes": scopes}
        if data_scope:
            payload["data_scope"] = data_scope
        r = client.post("/agent-access/grants", json=payload)
        grant_id = r.json()["id"]
        r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
        tokens[label] = {"token": r.json()["token"], "grant_id": grant_id}
    return {"owner_id": owner_id, "book_id": book.id, "tokens": tokens}


def _rest(db_session: Session, path: str, token: str):
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app).get(path, headers={"Authorization": f"Bearer {token}"})


def _mcp(db_session: Session, token: str, name: str, arguments: dict):
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    # CHK-077/BUG-214：网络门禁要求 IP 来源；BUG-208：params 携带 _meta
    c = TestClient(app, client=("127.0.0.1", 50000))
    return c.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": arguments, "_meta": {}}},
        headers={"Authorization": f"Bearer {token}",
                 "MCP-Protocol-Version": "2026-07-28"},
    )


# ── 同一 Grant，两条入口一致 ──


def test_same_grant_allow_on_both(mcp_on, world: dict, db_session: Session) -> None:
    token = world["tokens"]["pilot"]["token"]
    r_rest = _rest(db_session, "/api/v1/books", token)
    assert r_rest.status_code == 200
    r_mcp = _mcp(db_session, token, "bookshelf_search_books", {"query": "Parity"})
    assert r_mcp.status_code == 200
    assert r_mcp.json()["result"]["isError"] is False


def test_missing_scope_denied_on_both(mcp_on, world: dict, db_session: Session) -> None:
    token = world["tokens"]["no_scope"]["token"]
    assert _rest(db_session, "/api/v1/books", token).status_code == 403

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    c = TestClient(app, client=("127.0.0.1", 50000))
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                             "params": {"_meta": {}}},
               headers={"Authorization": f"Bearer {token}",
                        "MCP-Protocol-Version": "2026-07-28"})
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "SCOPE_DENIED"


def test_revocation_fails_on_both(mcp_on, world: dict, db_session: Session, client: TestClient) -> None:
    entry = world["tokens"]["pilot"]
    assert _rest(db_session, "/api/v1/books", entry["token"]).status_code == 200
    client.delete(f"/agent-access/grants/{entry['grant_id']}")
    assert _rest(db_session, "/api/v1/books", entry["token"]).status_code == 401
    r = _mcp(db_session, entry["token"], "bookshelf_search_books", {"query": "Parity"})
    assert r.status_code == 401


def test_mcp_is_strict_subset_of_rest(mcp_on, world: dict, db_session: Session) -> None:
    """旧语义 Grant：REST 放行（books:read 有效），MCP 按试点门禁更严——
    方向恒为"MCP ⊆ REST"，不得出现 REST 拒绝而 MCP 放行的反向旁路。"""
    legacy = world["tokens"]["legacy"]["token"]
    assert _rest(db_session, "/api/v1/books", legacy).status_code == 200
    r = _mcp(db_session, legacy, "bookshelf_search_books", {"query": "Parity"})
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "PILOT_GRANT_REQUIRED"


def test_mcp_fields_subset_of_rest_detail(mcp_on, world: dict, db_session: Session) -> None:
    """MCP 详情字段 ⊆ REST 授权详情字段；L3 数据两条入口都按 Scope 收敛。"""
    token = world["tokens"]["pilot"]["token"]
    r_rest = _rest(db_session, f"/api/v1/books/{world['book_id']}", token)
    assert r_rest.status_code == 200
    rest_data = r_rest.json()["data"]
    # books:read-only Grant：REST 也不下发 L3 子资源（BUG-166 口径）
    assert "reading_notes" not in rest_data
    assert "purchase_records" not in rest_data

    r_mcp = _mcp(db_session, token, "bookshelf_get_book", {"book_id": world["book_id"]})
    assert r_mcp.status_code == 200
    mcp_data = r_mcp.json()["result"]["structuredContent"]
    # MCP 字段是 REST 白名单的安全子集：无封面 URL / 无标签
    assert "cover_thumbnail_url" not in mcp_data
    assert "public_tags" not in mcp_data
    assert set(mcp_data.keys()) <= {
        "id", "title", "subtitle", "authors", "translators", "publisher",
        "publish_date", "edition", "language", "page_count", "category",
        "summary", "availability", "cover_thumbnail_url", "public_tags",
    }
    dumped = str(mcp_data)
    assert "PARITY_L3_NOTE" not in dumped and "PARITY_CHANNEL" not in dumped


# ── 限流 Profile 隔离（清单第 4 点：额度互不覆盖） ──


def test_rate_limit_profiles_isolated(mcp_on, world: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_rate_limit_per_minute", 1)
    token = world["tokens"]["pilot"]["token"]

    # MCP 额度耗尽 → 429
    assert _mcp(db_session, token, "bookshelf_search_books", {"query": "Parity"}).status_code == 200
    assert _mcp(db_session, token, "bookshelf_search_books", {"query": "Parity"}).status_code == 429

    # 同来源 IP 的 Public Catalog 请求不受 MCP 额度影响（独立 bucket/Profile）
    from app.services.trusted_network import evaluate_trust  # noqa: F401 — 仅为说明依赖
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    c = TestClient(app, client=("127.0.0.1", 50000))
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "lan_shared")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 200, "Public Catalog 额度不得被 MCP 占用"
