"""CHK-072 修复回归：BUG-190~195 + MCP 专用 Grant 门禁。

BUG-190 guest 角色违例（schema 收紧 + 未知角色 fail-closed）
BUG-191 /stats 非授权主体只返回本人统计
BUG-192 书籍详情内嵌 L3 子资源按主体归属过滤
BUG-193 /auth/login 失败计数限流
BUG-194 缩略图缓存键冲突与失效
BUG-195 MCP initialize 限流/审计、bool 输入、游标错误映射
门禁   专用试点 Grant：scopes 必须恰为 {books:read}
"""
from __future__ import annotations

import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, Member, PurchaseRecord, ReadingLog, ReadingNote, ReadingProgress
from app.services import rate_limit, security_audit
from app.utils.book_helpers import serialize_json_list
from tests.test_agent_access_e2e import _ensure_owner, _init_owner_password


# ── 测试世界 ──


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    owner_id = client.get("/auth/session").json()["member_id"]
    r = client.post("/api/v1/members", json={"name": "成员乙", "role": "member"})
    assert r.status_code == 201, r.text
    member_b = r.json()["data"]["id"]
    client.post("/api/v1/members/bind", json={
        "member_id": member_b, "channel": "feishu", "external_user_id": "ou_chk072_b",
    })
    book = Book(title="CHK072 书", authors=serialize_json_list(["作者"]))
    db_session.add(book)
    db_session.commit()
    db_session.add_all([
        ReadingProgress(book_id=book.id, member_id=owner_id, status="reading"),
        ReadingProgress(book_id=book.id, member_id=member_b, status="finished"),
        ReadingNote(book_id=book.id, member_id=owner_id, content_md="OWNER_NOTE_SENTINEL"),
        ReadingNote(book_id=book.id, member_id=member_b, content_md="MEMBER_B_NOTE"),
        PurchaseRecord(book_id=book.id, buyer_member_id=owner_id, price=100.0),
        PurchaseRecord(book_id=book.id, buyer_member_id=member_b, price=7.0),
        ReadingLog(book_id=book.id, member_id=owner_id, log_date="2026-08-20", pages_read=50),
        ReadingLog(book_id=book.id, member_id=member_b, log_date="2026-08-21", pages_read=5),
    ])
    db_session.commit()
    return {"owner_id": owner_id, "member_b": member_b, "book_id": book.id}


def _channel(ext: str) -> dict:
    return {"X-Channel": "feishu", "X-External-User-Id": ext}


def _make_client(db_session: Session) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


# ── BUG-190：guest ──


def test_member_create_rejects_guest(client: TestClient) -> None:
    r = client.post("/api/v1/members", json={"name": "访客", "role": "guest"})
    assert r.status_code == 422
    assert "guest" in r.text or "role" in r.text


def test_unknown_role_fails_closed_at_runtime(client: TestClient, db_session: Session, world: dict) -> None:
    """历史脏角色（直接落库的 guest）经渠道访问：空能力集 fail-closed。"""
    guest = Member(name="脏数据访客", role="guest")
    db_session.add(guest)
    db_session.commit()
    client.post("/api/v1/members/bind", json={
        "member_id": guest.id, "channel": "feishu", "external_user_id": "ou_chk072_guest",
    })
    c = _make_client(db_session)
    r = c.get("/api/v1/books", headers=_channel("ou_chk072_guest"))
    assert r.status_code == 403
    r = c.get("/api/v1/stats", headers=_channel("ou_chk072_guest"))
    assert r.status_code == 403


# ── BUG-191：/stats 本人范围 ──


def test_stats_member_channel_gets_own_scope_only(client: TestClient, world: dict, db_session: Session) -> None:
    c = _make_client(db_session)
    r = c.get("/api/v1/stats", headers=_channel("ou_chk072_b"))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # 成员乙只有 7 元购买；成员列表只有本人；页数只有本人 5 页
    assert data["total_spent"] == 7.0
    assert data["purchase_count"] == 1
    assert data["reading_logs_pages_total"] == 5
    assert [m["id"] for m in data["members"]] == [world["member_b"]]
    assert all(row["spent"] in (0.0, 7.0) for row in data["by_year"])


def test_stats_web_owner_gets_household(client: TestClient, world: dict) -> None:
    r = client.get("/api/v1/stats")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total_spent"] == 107.0
    assert data["purchase_count"] == 2
    assert len(data["members"]) >= 2


def test_stats_agent_with_household_scope_gets_full(client: TestClient, world: dict, db_session: Session) -> None:
    r = client.post("/agent-access/clients", json={"display_name": "家庭统计 Agent"})
    cid = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": cid, "member_id": world["member_b"],
        "scopes": ["stats:read", "stats:household"],
    })
    r = client.post("/agent-access/tokens", json={"grant_id": r.json()["id"]})
    token = r.json()["token"]

    c = _make_client(db_session)
    r = c.get("/api/v1/stats", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total_spent"] == 107.0  # stats:household → 全家庭口径


def test_stats_agent_without_household_gets_bound_member_only(client: TestClient, world: dict, db_session: Session) -> None:
    r = client.post("/agent-access/clients", json={"display_name": "本人统计 Agent"})
    cid = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": cid, "member_id": world["member_b"], "scopes": ["stats:read"],
    })
    r = client.post("/agent-access/tokens", json={"grant_id": r.json()["id"]})
    token = r.json()["token"]

    c = _make_client(db_session)
    r = c.get("/api/v1/stats", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total_spent"] == 7.0
    assert [m["id"] for m in data["members"]] == [world["member_b"]]


# ── BUG-192：详情 L3 子资源归属过滤 ──


def test_book_detail_l3_filtered_for_member_channel(client: TestClient, world: dict, db_session: Session) -> None:
    c = _make_client(db_session)
    r = c.get(f"/api/v1/books/{world['book_id']}", headers=_channel("ou_chk072_b"))
    assert r.status_code == 200
    data = r.json()["data"]
    # 成员乙只看到自己的进度/笔记/购买
    assert all(p["member_id"] == world["member_b"] for p in data.get("reading_progress", []))
    assert data["reading_progress"] and data["reading_progress"][0]["status"] == "finished"
    assert "OWNER_NOTE_SENTINEL" not in json.dumps(data, ensure_ascii=False)
    assert all(n["member_id"] == world["member_b"] for n in data.get("reading_notes", []))
    assert all(p.get("buyer_member_id") == world["member_b"] for p in data.get("purchase_records", []))


def test_book_detail_owner_web_sees_all(client: TestClient, world: dict) -> None:
    r = client.get(f"/api/v1/books/{world['book_id']}")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data.get("reading_progress", [])) == 2
    assert "OWNER_NOTE_SENTINEL" in json.dumps(data, ensure_ascii=False)
    assert len(data.get("purchase_records", [])) == 2


def test_book_detail_agent_readonly_sees_no_l3(client: TestClient, world: dict, db_session: Session) -> None:
    r = client.post("/agent-access/clients", json={"display_name": "只读书目 Agent"})
    cid = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": cid, "member_id": world["owner_id"], "scopes": ["books:read"],
    })
    r = client.post("/agent-access/tokens", json={"grant_id": r.json()["id"]})
    token = r.json()["token"]

    c = _make_client(db_session)
    r = c.get(f"/api/v1/books/{world['book_id']}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert "reading_progress" not in data
    assert "purchase_records" not in data
    assert "reading_notes" not in data


# ── BUG-193：登录失败限流 ──


def test_login_brute_force_rate_limited(client: TestClient, monkeypatch) -> None:
    from app.config import settings
    _ensure_owner(client)
    _init_owner_password(client)
    monkeypatch.setattr(settings, "auth_login_rate_limit_per_minute", 3)
    rate_limit.reset()

    # 复用 owner Web 客户端直接打 /auth/login（该端点不依赖已有 Cookie）
    r = client.post("/auth/login", json={"password": "test-password-123"})
    assert r.status_code == 200, r.text
    for _ in range(3):
        r = client.post("/auth/login", json={"password": "wrong"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"password": "test-password-123"})
    assert r.status_code == 429
    assert r.headers.get("X-Error-Code") == "RATE_LIMITED"
    rate_limit.reset()


# ── BUG-194：缩略图缓存 ──


def test_thumbnail_cache_key_includes_extension_and_invalidates(client: TestClient, monkeypatch) -> None:
    from PIL import Image

    from app.api.v1.public_catalog import _ensure_thumbnail
    from app.config import settings

    covers = settings.covers_dir
    covers.mkdir(parents=True, exist_ok=True)
    jpg = covers / "chk072.jpg"
    png = covers / "chk072.png"
    Image.new("RGB", (800, 1200), "red").save(jpg)
    Image.new("RGB", (800, 1200), "blue").save(png)

    t_jpg = _ensure_thumbnail(jpg)
    t_png = _ensure_thumbnail(png)
    # 同名不同扩展：缓存键不冲突
    assert t_jpg != t_png and t_jpg.is_file() and t_png.is_file()

    # 源文件更新后缩略图失效重建
    old_mtime = t_jpg.stat().st_mtime
    future = time.time() + 100
    os.utime(jpg, (future, future))
    t_jpg2 = _ensure_thumbnail(jpg)
    assert t_jpg2 == t_jpg
    assert t_jpg2.stat().st_mtime >= old_mtime  # 已重建（mtime 刷新）
    assert t_png.stat().st_mtime < t_jpg2.stat().st_mtime  # 未受牵连


# ── BUG-195 + 专用 Grant 门禁（MCP） ──


@pytest.fixture()
def mcp_on(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "chk072-cursor-secret")
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "testserver")
    rate_limit.reset()
    security_audit.reset()
    yield
    rate_limit.reset()
    security_audit.reset()


@pytest.fixture()
def mcp_tokens(client: TestClient, world: dict) -> dict:
    tokens = {}
    for label, scopes, data_scope in (
        ("pure", ["books:read"], "household_shared"),
        ("mixed", ["books:read", "notes:read"], "household_shared"),
        ("legacy", ["books:read"], None),  # 旧语义 Grant：无显式 data_scope
    ):
        r = client.post("/agent-access/clients", json={"display_name": f"CHK072-{label}"})
        cid = r.json()["id"]
        payload = {"agent_client_id": cid, "member_id": world["owner_id"], "scopes": scopes}
        if data_scope:
            payload["data_scope"] = data_scope
        r = client.post("/agent-access/grants", json=payload)
        r = client.post("/agent-access/tokens", json={"grant_id": r.json()["id"]})
        tokens[label] = r.json()["token"]
    return tokens


def _mcp(db_session: Session, method: str, token: str, params: dict | None = None):
    c = _make_client(db_session)
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                  headers={"Authorization": f"Bearer {token}",
                           "MCP-Protocol-Version": "2026-07-28"})


def test_mcp_pilot_grant_gate(mcp_on, mcp_tokens: dict, db_session: Session) -> None:
    """混合 Scope 与旧语义 Grant 都不能访问 MCP——必须显式专用试点 Grant（BUG-197）。"""
    r = _mcp(db_session, "tools/list", mcp_tokens["pure"])
    assert r.status_code == 200
    for label in ("mixed", "legacy"):
        r = _mcp(db_session, "tools/list", mcp_tokens[label])
        assert r.status_code == 403, label
        assert r.headers.get("X-Error-Code") == "PILOT_GRANT_REQUIRED", label


def test_mcp_initialize_rate_limited_and_audited(mcp_on, mcp_tokens: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_rate_limit_per_minute", 2)
    assert _mcp(db_session, "server/discover", mcp_tokens["pure"]).status_code == 200
    assert _mcp(db_session, "server/discover", mcp_tokens["pure"]).status_code == 200
    r = _mcp(db_session, "server/discover", mcp_tokens["pure"])
    assert r.status_code == 429

    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    payloads = [e.payload or "" for e in events]
    assert any('"method": "server/discover"' in p and '"outcome": "allow"' in p for p in payloads)


def test_mcp_bool_inputs_rejected(mcp_on, mcp_tokens: dict, db_session: Session) -> None:
    r = _mcp(db_session, "tools/call", mcp_tokens["pure"], {
        "name": "bookshelf_search_books", "arguments": {"query": "CHK072", "limit": True},
    })
    assert r.json()["result"]["structuredError"]["code"] == "LIMIT_INVALID"
    r = _mcp(db_session, "tools/call", mcp_tokens["pure"], {
        "name": "bookshelf_get_book", "arguments": {"book_id": True},
    })
    assert r.json()["result"]["structuredError"]["code"] == "BOOK_ID_INVALID"


def test_mcp_invalid_cursor_stable_error(mcp_on, mcp_tokens: dict, db_session: Session) -> None:
    r = _mcp(db_session, "tools/call", mcp_tokens["pure"], {
        "name": "bookshelf_search_books",
        "arguments": {"query": "CHK072", "cursor": "v1.2.deadbeefdeadbeef"},
    })
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "INVALID_CURSOR"


# ── 提示项：ENDPOINT_REGISTRY 漂移防护 ──


def test_endpoint_registry_covers_all_v1_routes() -> None:
    """注册表 ↔ 实际路由漂移防护（CHK-072 提示项）。"""
    from tests.test_permission_baseline_matrix import ENDPOINT_REGISTRY

    registered = {(spec.method, spec.path) for spec in ENDPOINT_REGISTRY}
    actual: set[tuple[str, str]] = set()
    for route in app.routes:
        if not getattr(route, "methods", None):
            continue
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1"):
            continue
        for method in route.methods:
            if method in ("GET", "POST", "PATCH", "DELETE", "PUT"):
                actual.add((method, path))
    known_unlisted = {
        ("POST", "/api/v1/members/bind"),      # 引导期专用，注册表以 owner 专用描述
        ("GET", "/api/v1/public-health"),       # L0 公开端点
    }
    missing = actual - registered - known_unlisted
    assert not missing, f"端点未登记进 ENDPOINT_REGISTRY: {sorted(missing)}"
