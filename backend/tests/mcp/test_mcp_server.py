"""MCP /mcp 端到端测试（WBS-MCP-3/4/5/7 核心，合成数据）。

覆盖：开关/精确路径/协议版本、认证拒绝矩阵（无 Token/坏 Token/Cookie/渠道头/
缺 Scope）、server/discover/tools/list/tools/call、搜索约束（必带条件、limit 边界、
游标签发与篡改）、详情防枚举、限流、撤销下一请求生效、隐私哨兵零命中、
共享审计事件。

CHK-077 契约更新：请求必须携带 params._meta（2026-07-28 无状态契约）；
_test 客户端对端固定为回环 IP（网络门禁：默认仅回环可信）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, BookCopy, BookTag, Member, PurchaseRecord, ReadingNote, Tag
from app.services import rate_limit, security_audit
from app.utils.book_helpers import serialize_json_list

SENTINELS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "privacy_sentinels.json").read_text(encoding="utf-8")
)


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
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "unit-test-cursor-secret-high-entropy")
    # CHK-073：TestClient 的 Host 为 testserver，需进 allowlist（生产默认仅回环）
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "testserver")


@pytest.fixture()
def seeded(client: TestClient, db_session: Session) -> dict:
    """owner + 含敏感哨兵的合成书目 + books:read 试点 Grant。"""
    owner_id = client.get("/auth/session").json()["member_id"]
    member = Member(name=SENTINELS["member_name"], role="member")
    db_session.add(member)
    db_session.commit()

    tag = Tag(name=SENTINELS["tag"])
    db_session.add(tag)
    db_session.commit()

    books = []
    for i in range(1, 4):
        b = Book(
            title=f"MCP合成书{i}", authors=serialize_json_list([f"合成作者{i}"]),
            publisher=f"合成出版社{i}", category="科幻", language="zh",
            isbn13=f"978730000000{i}", cover_path=f"{SENTINELS['cover_path']}{i}.jpg",
            extra=json.dumps({"k": SENTINELS["custom_field"]}, ensure_ascii=False),
        )
        db_session.add(b)
        db_session.commit()
        db_session.add(BookCopy(book_id=b.id, status="in_shelf", location="SENTINEL_LOC"))
        db_session.add(BookTag(book_id=b.id, tag_id=tag.id))
        db_session.add(ReadingNote(book_id=b.id, member_id=member.id, content_md=SENTINELS["private_note"]))
        db_session.add(PurchaseRecord(
            book_id=b.id, buyer_member_id=member.id, price=1.0, channel=SENTINELS["purchase_channel"],
        ))
        db_session.commit()
        books.append(b)

    r = client.post("/agent-access/clients", json={"display_name": "MCP 试点 Agent"})
    agent_client_id = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id, "member_id": owner_id,
        "scopes": ["books:read"], "data_scope": "household_shared",
    })
    grant_id = r.json()["id"]
    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # 无 books:read 的对照组 Grant
    r = client.post("/agent-access/clients", json={"display_name": "无权限 Agent"})
    r = client.post("/agent-access/grants", json={
        "agent_client_id": r.json()["id"], "member_id": owner_id, "scopes": ["stats:read"],
    })
    r = client.post("/agent-access/tokens", json={"grant_id": r.json()["id"]})
    other_token = r.json()["token"]

    return {
        "owner_id": owner_id, "books": books, "token": token,
        "other_token": other_token, "grant_id": grant_id,
        "agent_client_id": agent_client_id,
    }


def _mcp_client(db_session: Session, cookies: dict | None = None) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    # CHK-077/BUG-214：网络门禁要求来源为 IP；默认仅回环可信
    c = TestClient(app, client=("127.0.0.1", 50000))
    if cookies:
        for k, v in cookies.items():
            c.cookies.set(k, v, domain="testserver.local")
    return c


def _rpc(c: TestClient, method: str, params: dict | None = None, token: str | None = None,
         headers: dict | None = None, path: str = "/mcp", raw_body: dict | None = None):
    h = {"Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    if raw_body is not None:
        body = raw_body
    else:
        # BUG-208：params 必须携带 _meta 对象（每请求自描述元数据）
        body = {"jsonrpc": "2.0", "id": 1, "method": method,
                "params": {**(params or {}), "_meta": {}}}
    return c.post(path, json=body, headers=h)


def _call(c: TestClient, token: str, name: str, arguments: dict, headers: dict | None = None):
    return _rpc(c, "tools/call", {"name": name, "arguments": arguments}, token=token, headers=headers)


# ── 开关 / 路径 / 协议 ──


def test_mcp_disabled_returns_404(db_session: Session) -> None:
    c = _mcp_client(db_session)
    assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 404


def test_mcp_trailing_slash_404_without_redirect(mcp_on, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, follow_redirects=False)
    assert r.status_code == 404


def test_mcp_get_method_not_allowed(mcp_on, db_session: Session) -> None:
    c = _mcp_client(db_session)
    assert c.get("/mcp").status_code == 405


def test_mcp_protocol_version_header_required(mcp_on, seeded: dict, db_session: Session) -> None:
    """BUG-196：缺协议版本头不再放行。"""
    c = _mcp_client(db_session)
    r = _rpc(c, "server/discover", token=seeded["token"], headers={"MCP-Protocol-Version": ""})
    # 空头等效缺失：httpx 会去掉空值头，再显式不带头发一次
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
               headers={"Authorization": f"Bearer {seeded['token']}"})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "PROTOCOL_VERSION_REQUIRED"


def test_mcp_protocol_version_allowlist(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "server/discover", token=seeded["token"],
             headers={"MCP-Protocol-Version": "2025-03-26"})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "PROTOCOL_VERSION_REJECTED"
    r = _rpc(c, "server/discover", token=seeded["token"],
             headers={"MCP-Protocol-Version": "2026-07-28"})
    assert r.status_code == 200


def test_mcp_enabled_without_cursor_secret_500(mcp_on, monkeypatch, seeded: dict, db_session: Session) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", None)
    c = _mcp_client(db_session)
    r = _rpc(c, "initialize", token=seeded["token"])
    assert r.status_code == 500


# ── 认证拒绝矩阵（WBS-MCP-4 Task 4.1） ──


def test_no_token_401_auth_required(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list")
    assert r.status_code == 401
    assert r.headers.get("X-Error-Code") == "AUTH_REQUIRED"


def test_bad_token_401_token_invalid(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", token="hbs_at_deadbeef_deadbeef")
    assert r.status_code == 401
    assert r.headers.get("X-Error-Code") == "TOKEN_INVALID"


def test_web_cookie_rejected(mcp_on, seeded: dict, db_session: Session, client: TestClient) -> None:
    session_cookie = client.cookies.get("hbs_session")
    c = _mcp_client(db_session, cookies={"hbs_session": session_cookie})
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
               headers={"MCP-Protocol-Version": "2026-07-28"})
    assert r.status_code == 401
    assert r.headers.get("X-Error-Code") == "AUTH_REQUIRED"


def test_channel_headers_rejected(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", headers={"X-Channel": "feishu", "X-External-User-Id": "ou_x"})
    assert r.status_code == 401


def test_token_without_scope_403(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", token=seeded["other_token"])
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "SCOPE_DENIED"


def test_discover_allowed_with_any_valid_token(mcp_on, seeded: dict, db_session: Session) -> None:
    """server/discover：自描述发现（该版本已移除 initialize），任何有效 Token 可用。

    BUG-208：DiscoverResult = supportedVersions + resultType，
    serverInfo/capabilities 在 result._meta（不再自定义顶层 protocolVersion）。
    """
    c = _mcp_client(db_session)
    r = _rpc(c, "server/discover", token=seeded["other_token"])
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["resultType"] == "discover"
    meta = result["_meta"]
    assert meta["serverInfo"]["name"] == "home_bookshelf_mcp"
    assert "version" in meta["serverInfo"]
    assert meta["capabilities"] == {"tools": {}}
    # 顶层不再有自定义 protocolVersion/serverInfo
    assert "protocolVersion" not in result
    assert "serverInfo" not in result


def test_initialize_removed_by_protocol(mcp_on, seeded: dict, db_session: Session) -> None:
    """BUG-196：initialize 已被 2026-07-28 移除——返回 -32601 而非 200。"""
    c = _mcp_client(db_session)
    r = _rpc(c, "initialize", token=seeded["token"])
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


def test_malformed_frame_rejected_not_500(mcp_on, seeded: dict, db_session: Session) -> None:
    """BUG-195 补充：jsonrpc 版本/params 非对象 → 稳定 400；arguments 非对象 → -32602。"""
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", token=seeded["token"],
             raw_body={"jsonrpc": "1.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 400
    r = _rpc(c, "tools/list", token=seeded["token"],
             raw_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": ["not", "dict"]})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "PARAMS_META_REQUIRED"
    r = _rpc(c, "tools/call", token=seeded["token"],
             params={"name": "bookshelf_search_books", "arguments": ["not", "dict"]})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


def test_host_and_origin_rejected(mcp_on, seeded: dict, db_session: Session) -> None:
    """BUG-196：恶意 Host → 421（DNS Rebinding 防护）；恶意 Origin → 403。"""
    c = _mcp_client(db_session)
    r = _rpc(c, "server/discover", token=seeded["token"], headers={"Host": "evil.example.com"})
    assert r.status_code == 421
    assert r.headers.get("X-Error-Code") == "HOST_REJECTED"
    r = _rpc(c, "server/discover", token=seeded["token"],
             headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "ORIGIN_REJECTED"


def test_old_grant_without_data_scope_rejected(mcp_on, seeded: dict, db_session: Session,
                                               client: TestClient) -> None:
    """BUG-197：旧语义 Grant（books:read 但无显式 data_scope）不能进 MCP。"""
    owner_id = seeded["owner_id"]
    r = client.post("/agent-access/clients", json={"display_name": "旧语义 Grant"})
    cid = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": cid, "member_id": owner_id, "scopes": ["books:read"],
    })
    r = client.post("/agent-access/tokens", json={"grant_id": r.json()["id"]})
    legacy_token = r.json()["token"]

    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", token=legacy_token)
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "PILOT_GRANT_REQUIRED"


def test_audit_event_carries_contract_fields(mcp_on, seeded: dict, db_session: Session) -> None:
    """BUG-198：审计事件含 request_id/protocol_version/grant/tool/耗时/结果数。"""
    c = _mcp_client(db_session)
    _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"})
    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    allow = [e for e in events if '"outcome": "allow"' in (e.payload or "") and "tool_name" in (e.payload or "")]
    assert allow, "缺少带 tool_name 的 allow 审计"
    payload = json.loads(allow[0].payload)
    for key in ("request_id", "protocol_version", "grant_id", "grant_version",
                "tool_name", "args_digest", "result_count", "duration_ms", "data_scope",
                "client_info", "agent_client_id", "token_prefix",
                "source_ip", "trusted_proxy_result"):
        assert key in payload["details"], key
    assert payload["details"]["data_scope"] == "household_shared"
    assert payload["details"]["agent_client_id"] == seeded["agent_client_id"]
    assert payload["details"]["token_prefix"].startswith("hbs_at_")
    assert payload["details"]["source_ip"] == "127.0.0.1"
    assert payload["details"]["client_info"]["name"] == "MCP 试点 Agent"


def test_audit_failure_fails_closed(mcp_on, seeded: dict, db_session: Session, monkeypatch) -> None:
    """BUG-198：allow 路径审计写入失败 → 503，绝不返回真实数据。"""
    from unittest.mock import patch

    with patch.object(security_audit, "log_security_event", return_value=security_audit.AUDIT_FAILED):
        c = _mcp_client(db_session)
        r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"})
        assert r.status_code == 503
        assert r.headers.get("X-Error-Code") == "AUDIT_UNAVAILABLE"


def test_cursor_bound_to_query_filters(mcp_on, seeded: dict, db_session: Session) -> None:
    """BUG-201：合法游标换查询条件/页长后失效。"""
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP", "limit": 2})
    cursor = r.json()["result"]["structuredContent"]["next_cursor"]
    assert cursor
    # 换查询条件
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "MCP合成书1", "limit": 2, "cursor": cursor})
    assert r.json()["result"]["structuredError"]["code"] == "INVALID_CURSOR"
    # 换页长
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "MCP", "limit": 1, "cursor": cursor})
    assert r.json()["result"]["structuredError"]["code"] == "INVALID_CURSOR"
    # 原条件原页长仍可用
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "MCP", "limit": 2, "cursor": cursor})
    assert r.json()["result"]["isError"] is False


# ── 工具面 ──


def test_tools_list_order_and_schema(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", token=seeded["token"])
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["bookshelf_search_books", "bookshelf_get_book"]


def test_unknown_tool_jsonrpc_error(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "list_all_books", {})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


def test_search_requires_filter(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books", {})
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "QUERY_REQUIRED"


def test_search_limit_bounds(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    for bad in (0, 21):
        r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP", "limit": bad})
        assert r.json()["result"]["structuredError"]["code"] == "LIMIT_INVALID", bad


def test_search_and_get_roundtrip(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP合成书1"})
    result = r.json()["result"]
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["count"] == 1
    assert data["items"][0]["title"] == "MCP合成书1"
    assert data["items"][0]["availability"] == "in_shelf"

    book_id = data["items"][0]["id"]
    r = _call(c, seeded["token"], "bookshelf_get_book", {"book_id": book_id})
    detail = r.json()["result"]["structuredContent"]
    assert detail["title"] == "MCP合成书1"


def test_get_book_not_found_protected(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_get_book", {"book_id": 999999})
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "BOOK_NOT_FOUND"


# ── 分页与游标 ──


def test_pagination_cursor_roundtrip_and_tamper(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP", "limit": 2})
    data = r.json()["result"]["structuredContent"]
    assert data["count"] == 2 and data["has_more"] is True
    cursor = data["next_cursor"]
    assert cursor

    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP", "limit": 2, "cursor": cursor})
    data2 = r.json()["result"]["structuredContent"]
    assert data2["count"] == 1 and data2["has_more"] is False
    assert {i["id"] for i in data["items"]}.isdisjoint({i["id"] for i in data2["items"]})

    # 篡改页码（签名不符）
    tampered = cursor.rsplit(".", 1)[0] + ".deadbeefdeadbeef"
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP", "limit": 2, "cursor": tampered})
    assert r.json()["result"]["structuredError"]["code"] == "INVALID_CURSOR"


# ── 隐私哨兵（零命中） ──


def test_no_sentinel_leakage_any_output(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    parts = [
        json.dumps(_rpc(c, "tools/list", token=seeded["token"]).json(), ensure_ascii=False),
    ]
    for args in ({"query": "MCP"}, {"query": SENTINELS["isbn"]}):
        r = _call(c, seeded["token"], "bookshelf_search_books", args)
        parts.append(json.dumps(r.json(), ensure_ascii=False))
    for b in seeded["books"]:
        r = _call(c, seeded["token"], "bookshelf_get_book", {"book_id": b.id})
        parts.append(json.dumps(r.json(), ensure_ascii=False))
    dump = "\n".join(parts)

    for key in ("member_name", "private_note", "purchase_channel", "file_path",
                "cover_path", "isbn", "custom_field", "tag"):
        assert SENTINELS[key] not in dump, key
    # 输出键面：无封面 URL / 标签 / 敏感键
    r = _call(c, seeded["token"], "bookshelf_get_book", {"book_id": seeded["books"][0].id})
    out = r.json()["result"]["structuredContent"]
    assert set(out.keys()) == {
        "id", "title", "subtitle", "authors", "translators", "publisher",
        "publish_date", "edition", "language", "page_count", "category",
        "summary", "availability",
    }


# ── 限流 / 撤销 / 审计 ──


def test_rate_limit_429(mcp_on, seeded: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_rate_limit_per_minute", 2)
    c = _mcp_client(db_session)
    assert _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"}).status_code == 200
    assert _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"}).status_code == 200
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"})
    assert r.status_code == 429
    assert r.headers.get("X-Error-Code") == "RATE_LIMITED"
    assert "Retry-After" in r.headers


def test_revocation_next_request_fails(mcp_on, seeded: dict, db_session: Session, client: TestClient) -> None:
    c = _mcp_client(db_session)
    assert _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"}).status_code == 200
    client.delete(f"/agent-access/grants/{seeded['grant_id']}")
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"})
    assert r.status_code == 401


def test_audit_events_recorded(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    _rpc(c, "tools/list")  # 401 → deny
    _call(c, seeded["token"], "bookshelf_search_books", {"query": "MCP"})  # allow
    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    payloads = [e.payload or "" for e in events]
    assert any('"outcome": "deny"' in p and "AUTH_REQUIRED" in p for p in payloads)
    assert any('"outcome": "allow"' in p for p in payloads)
