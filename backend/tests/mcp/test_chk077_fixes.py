"""CHK-077 回归测试：外部评审 BUG-208~BUG-216 修复验证（合成数据）。

覆盖：
- BUG-208 wire 契约：params._meta 必填、Mcp-Method/Mcp-Name 头体一致性、
  discover 返回 supportedVersions/resultType（serverInfo 在 result._meta）；
- BUG-209 审计 fail-closed 不被抑制绕过：写失败不登记抑制；discover 放行
  不抑制工具调用审计；deny 抑制键按 method/tool 分维度；
- BUG-210 空白搜索条件不得遍历全库；
- BUG-211 未知方法消耗请求级限流预算（方法名不进限流键）；
- BUG-212 请求/响应体硬上限、入参 maxLength 运行时校验、页长收敛、
  游标密钥熵/不复用检查；
- BUG-213 授权范围变更 -> 版本递增 + 旧令牌吊销（新令牌绑定新版本）；
- BUG-214 可信网络档：非回环须命中 MCP_TRUSTED_CIDRS；HTTPS 档默认强制；
- BUG-215 数据库异常/未捕获异常 -> 稳定可重试错误 + 完整审计；
- BUG-216 tools/list 声明 outputSchema，输出经校验器验证。
"""
from __future__ import annotations

import json
import types
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_db
from app.main import app
from app.models import AgentGrant
from app.services import rate_limit, security_audit

_LOOPBACK_PEER = ("127.0.0.1", 50000)


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
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "chk077-cursor-secret-high-entropy-0123456789")
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "testserver")


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    """owner + 试点 Grant（books:read + household_shared）+ 令牌。"""
    owner_id = client.get("/auth/session").json()["member_id"]
    r = client.post("/agent-access/clients", json={"display_name": "CHK077 Agent"})
    cid = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": cid, "member_id": owner_id,
        "scopes": ["books:read"], "data_scope": "household_shared",
    })
    grant_id = r.json()["id"]
    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    return {"owner_id": owner_id, "client_id": cid,
            "grant_id": grant_id, "token": r.json()["token"]}


def _mcp_client(db_session: Session, peer=_LOOPBACK_PEER) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, client=peer)


def _rpc(c: TestClient, method: str, params: dict | None = None, token: str | None = None,
         headers: dict | None = None, raw_body: dict | None = None):
    h = {"Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    if raw_body is not None:
        body = raw_body
    else:
        body = {"jsonrpc": "2.0", "id": 1, "method": method,
                "params": {**(params or {}), "_meta": {}}}
    return c.post("/mcp", json=body, headers=h)


def _call(c: TestClient, token: str, name: str, arguments: dict, headers: dict | None = None):
    return _rpc(c, "tools/call", {"name": name, "arguments": arguments}, token=token, headers=headers)


class _BoomSession:
    def __enter__(self):
        raise RuntimeError("audit db down")

    def __exit__(self, *args):
        return False


# ── BUG-208：wire 契约（params._meta / 网关路由头 / discover 形状） ──


def test_params_meta_required(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", token=world["token"],
             raw_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "PARAMS_META_REQUIRED"
    r = _rpc(c, "tools/list", token=world["token"],
             raw_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": "not-dict"}})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "PARAMS_META_REQUIRED"


def test_gateway_header_body_consistency(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    # Mcp-Method 与请求体不符 -> 400
    r = _rpc(c, "tools/list", token=world["token"], headers={"Mcp-Method": "tools/call"})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "HEADER_BODY_MISMATCH"
    # Mcp-Method 一致 -> 正常
    r = _rpc(c, "tools/list", token=world["token"], headers={"Mcp-Method": "tools/list"})
    assert r.status_code == 200
    # Mcp-Name 仅适用于命名方法 tools/call：不匹配 -> 400
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "x"},
              headers={"Mcp-Name": "bookshelf_get_book"})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "HEADER_BODY_MISMATCH"
    # Mcp-Name 匹配 -> 正常
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "x"},
              headers={"Mcp-Name": "bookshelf_search_books"})
    assert r.status_code == 200
    # Mcp-Name 出现在非命名方法上 -> 400（不得被强制用于 tools/list）
    r = _rpc(c, "tools/list", token=world["token"], headers={"Mcp-Name": "tools/list"})
    assert r.status_code == 400
    assert r.headers.get("X-Error-Code") == "HEADER_BODY_MISMATCH"


def test_discover_shape_matches_protocol(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "server/discover", token=world["token"])
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["resultType"] == "discover"
    assert result["_meta"]["serverInfo"]["name"] == "home_bookshelf_mcp"
    assert result["_meta"]["capabilities"] == {"tools": {}}
    assert "protocolVersion" not in result and "serverInfo" not in result


# ── BUG-209：审计 fail-closed 不被抑制绕过 ──


def test_audit_write_failure_not_suppressed(mcp_on, world: dict, db_session: Session,
                                            monkeypatch) -> None:
    """写失败后第二次调用不得变成 suppressed 而放行数据。"""
    monkeypatch.setattr(security_audit, "db_module",
                        types.SimpleNamespace(SessionLocal=_BoomSession))
    c = _mcp_client(db_session)
    r1 = _call(c, world["token"], "bookshelf_search_books", {"query": "书"})
    assert r1.status_code == 503
    assert r1.headers.get("X-Error-Code") == "AUDIT_UNAVAILABLE"
    # 旧实现此处会命中"失败前登记的抑制窗口"-> suppressed -> 200 泄漏真实数据
    r2 = _call(c, world["token"], "bookshelf_search_books", {"query": "书"})
    assert r2.status_code == 503
    assert r2.headers.get("X-Error-Code") == "AUDIT_UNAVAILABLE"


def test_discover_allow_does_not_suppress_tool_call_audit(mcp_on, world: dict,
                                                          db_session: Session,
                                                          monkeypatch) -> None:
    """discover 放行审计不得抑制后续工具调用的 allow 审计。"""
    c = _mcp_client(db_session)
    assert _rpc(c, "server/discover", token=world["token"]).status_code == 200
    # discover 审计写成功后，工具调用审计写失败仍须 fail-closed
    monkeypatch.setattr(security_audit, "db_module",
                        types.SimpleNamespace(SessionLocal=_BoomSession))
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "书"})
    assert r.status_code == 503
    assert r.headers.get("X-Error-Code") == "AUDIT_UNAVAILABLE"


def test_deny_audit_suppression_keyed_by_tool(mcp_on, world: dict, db_session: Session) -> None:
    """不同工具的 deny 事件分键抑制：两个都须落库（旧实现共用键，第二个被吞）。"""
    c = _mcp_client(db_session)
    _call(c, world["token"], "bookshelf_search_books", {})  # QUERY_REQUIRED
    _call(c, world["token"], "bookshelf_get_book", {"book_id": 999999})  # BOOK_NOT_FOUND
    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    payloads = [e.payload or "" for e in events]
    assert any("QUERY_REQUIRED" in p for p in payloads)
    assert any("BOOK_NOT_FOUND" in p for p in payloads)


# ── BUG-210：空白搜索条件 ──


def test_blank_search_query_rejected(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    for args in ({"query": "   "}, {"author": "  \t "}, {"query": " ", "category": " "}):
        r = _call(c, world["token"], "bookshelf_search_books", args)
        result = r.json()["result"]
        assert result["isError"] is True, args
        assert result["structuredError"]["code"] == "QUERY_REQUIRED", args


# ── BUG-211：未知方法消耗限流预算 ──


def test_unknown_methods_consume_rate_limit(mcp_on, world: dict, db_session: Session,
                                            monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_rate_limit_per_minute", 3)
    c = _mcp_client(db_session)
    # 三个互不相同的未知方法各自 -32601，但都消耗同一 (Client+Grant) 全局预算
    for i in range(3):
        r = _rpc(c, f"vendor/unknown/{i}", token=world["token"])
        assert r.status_code == 200
        assert r.json()["error"]["code"] == -32601
    # 预算耗尽后合法请求同样 429（方法名刷桶不再放大内存/绕过限流）
    r = _rpc(c, "tools/list", token=world["token"])
    assert r.status_code == 429
    assert r.headers.get("X-Error-Code") == "RATE_LIMITED"


# ── BUG-212：传输与输入硬上限 ──


def test_request_body_too_large_413(mcp_on, world: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_max_request_body_bytes", 512)
    c = _mcp_client(db_session)
    r = c.post(
        "/mcp",
        content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                            "params": {"_meta": {}, "pad": "x" * 2048}}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "MCP-Protocol-Version": "2026-07-28",
                 "Authorization": f"Bearer {world['token']}"},
    )
    assert r.status_code == 413
    assert r.headers.get("X-Error-Code") == "REQUEST_TOO_LARGE"


def test_response_body_too_large_guard(mcp_on, world: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_max_response_body_bytes", 64)
    c = _mcp_client(db_session)
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "书"})
    assert r.status_code == 500
    assert r.headers.get("X-Error-Code") == "RESPONSE_TOO_LARGE"


def test_input_maxlength_enforced_at_runtime(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "长" * 201})
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "PARAM_INVALID"


def test_page_size_clamped_to_frozen_contract(mcp_on, world: dict, db_session: Session,
                                              monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_max_page_size", 100)  # 越界配置（运行时改写）
    c = _mcp_client(db_session)
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "书", "limit": 25})
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "LIMIT_INVALID"
    # 收敛后的上限（20）内正常
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "书", "limit": 20})
    assert r.json()["result"]["isError"] is False


def test_cursor_secret_entropy_and_reuse_rejected(mcp_on, world: dict, db_session: Session,
                                                   monkeypatch) -> None:
    from app.config import settings
    c = _mcp_client(db_session)
    # 低熵密钥（< 32 字符）
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "short-secret")
    r = _rpc(c, "tools/list", token=world["token"])
    assert r.status_code == 500
    assert r.headers.get("X-Error-Code") == "CURSOR_SECRET_INVALID"
    # 复用渠道签名密钥
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "a" * 48)
    monkeypatch.setattr(settings, "channel_signing_secret", "a" * 48)
    r = _rpc(c, "tools/list", token=world["token"])
    assert r.status_code == 500
    assert r.headers.get("X-Error-Code") == "CURSOR_SECRET_INVALID"


def test_cursor_length_capped(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, world["token"], "bookshelf_search_books",
              {"query": "书", "cursor": "v1." + "9" * 200 + ".abcdef123456.0123456789abcdef"})
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "INVALID_CURSOR"


# ── BUG-213：授权版本绑定令牌 ──


def test_scope_change_bumps_version_and_revokes_tokens(mcp_on, world: dict,
                                                        db_session: Session,
                                                        client: TestClient) -> None:
    grant = db_session.get(AgentGrant, world["grant_id"])
    version_before = grant.version or 1

    # 同集合更新：不递增版本、不吊销令牌
    r = client.patch(f"/agent-access/grants/{world['grant_id']}", json={"scopes": ["books:read"]})
    assert r.status_code == 200
    db_session.expire_all()
    grant = db_session.get(AgentGrant, world["grant_id"])
    assert (grant.version or 1) == version_before
    c = _mcp_client(db_session)
    assert _rpc(c, "tools/list", token=world["token"]).status_code == 200

    # 范围实际变更：版本递增 + 旧令牌吊销
    r = client.patch(f"/agent-access/grants/{world['grant_id']}",
                     json={"scopes": ["books:read", "books:write"]})
    assert r.status_code == 200
    db_session.expire_all()
    grant = db_session.get(AgentGrant, world["grant_id"])
    assert (grant.version or 1) == version_before + 1

    # 旧令牌立即失效（吊销 -> 401），不是带着旧范围继续用
    r = _rpc(c, "tools/list", token=world["token"])
    assert r.status_code == 401
    assert r.headers.get("X-Error-Code") == "TOKEN_INVALID"

    # 新签发令牌绑定新版本：可用（混合 Scope 被试点门禁拦为 403，而非 401）
    r = client.post("/agent-access/tokens", json={"grant_id": world["grant_id"]})
    new_token = r.json()["token"]
    r = _rpc(c, "tools/list", token=new_token)
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "PILOT_GRANT_REQUIRED"

    # 恢复为纯 books:read（版本再次递增）后，混合期令牌同样失效
    r = client.patch(f"/agent-access/grants/{world['grant_id']}", json={"scopes": ["books:read"]})
    assert r.status_code == 200
    r = _rpc(c, "tools/list", token=new_token)
    assert r.status_code == 401
    r = client.post("/agent-access/tokens", json={"grant_id": world["grant_id"]})
    r = _rpc(c, "tools/list", token=r.json()["token"])
    assert r.status_code == 200


# ── BUG-214：可信网络与 HTTPS 档 ──


def test_untrusted_source_ip_denied(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session, peer=("203.0.113.9", 50000))
    r = _rpc(c, "tools/list", token=world["token"])
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "NETWORK_DENIED"


def test_lan_cidr_trusted_but_https_enforced(mcp_on, world: dict, db_session: Session,
                                             monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_trusted_cidrs", "192.168.1.0/24")
    c = _mcp_client(db_session, peer=("192.168.1.50", 50000))
    # 命中可信 CIDR 但 HTTP + 默认 HTTPS 档 -> 403
    r = _rpc(c, "tools/list", token=world["token"])
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "HTTPS_REQUIRED"


def test_lan_http_pilot_requires_explicit_optout(mcp_on, world: dict, db_session: Session,
                                                 monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_trusted_cidrs", "192.168.1.0/24")
    monkeypatch.setattr(settings, "mcp_require_https", False)
    c = _mcp_client(db_session, peer=("192.168.1.50", 50000))
    r = _rpc(c, "tools/list", token=world["token"])
    assert r.status_code == 200


def test_trusted_proxy_xff_resolution(mcp_on, world: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    monkeypatch.setattr(settings, "mcp_trusted_cidrs", "192.168.1.0/24")
    c = _mcp_client(db_session, peer=("10.0.0.5", 50000))
    # 代理还原出的真实客户端不可信 -> 403
    r = _rpc(c, "tools/list", token=world["token"], headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "NETWORK_DENIED"
    # 真实客户端在可信 CIDR 但代理未声明 X-Forwarded-Proto -> fail-closed
    r = _rpc(c, "tools/list", token=world["token"], headers={"X-Forwarded-For": "192.168.1.50"})
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "HTTPS_REQUIRED"
    # 声明 https 后放行
    r = _rpc(c, "tools/list", token=world["token"],
             headers={"X-Forwarded-For": "192.168.1.50", "X-Forwarded-Proto": "https"})
    assert r.status_code == 200


# ── BUG-215：数据库异常与未捕获异常的稳定错误 ──


def test_db_error_maps_to_retryable_tool_error(mcp_on, world: dict, db_session: Session) -> None:
    from app.mcp_server import server as mcp_server
    with patch.object(mcp_server, "_call_tool", side_effect=SQLAlchemyError("database is locked")):
        c = _mcp_client(db_session)
        r = _call(c, world["token"], "bookshelf_search_books", {"query": "书"})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is True
    err = result["structuredError"]
    assert err["code"] == "DB_BUSY"
    assert err["retryable"] is True
    assert err["request_id"].startswith("req_")
    # 无 SQL/异常细节泄露
    assert "database is locked" not in json.dumps(result)
    # 完整调用审计（deny + DB_BUSY）
    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    assert any("DB_BUSY" in (e.payload or "") for e in events)


def test_unexpected_error_maps_to_jsonrpc_internal(mcp_on, world: dict, db_session: Session) -> None:
    from app.mcp_server import server as mcp_server
    with patch.object(mcp_server, "_call_tool", side_effect=RuntimeError("boom")):
        c = _mcp_client(db_session)
        r = _call(c, world["token"], "bookshelf_search_books", {"query": "书"})
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == -32603
    assert body["error"]["data"]["retryable"] is True
    assert body["error"]["data"]["request_id"].startswith("req_")
    assert "boom" not in json.dumps(body)
    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    assert any("INTERNAL_ERROR" in (e.payload or "") for e in events)


# ── BUG-216：输出契约冻结 ──


def test_tools_list_declares_output_schema(mcp_on, world: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _rpc(c, "tools/list", token=world["token"])
    tools = r.json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["bookshelf_search_books", "bookshelf_get_book"]
    for t in tools:
        schema = t["outputSchema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        assert schema["required"]
    # 搜索输出 Schema 形状
    search = tools[0]["outputSchema"]
    assert set(search["properties"]) == {"items", "count", "has_more", "next_cursor"}
    book = tools[1]["outputSchema"]
    assert "id" in book["properties"] and "availability" in book["properties"]


def test_output_validator_rejects_contract_violations() -> None:
    from app.mcp_server.tools.catalog import (
        _BOOK_OUTPUT_SCHEMA,
        ToolError,
        validate_tool_output,
    )
    good = {
        "id": 1, "title": "t", "subtitle": None, "authors": [], "translators": [],
        "publisher": None, "publish_date": None, "edition": None, "language": None,
        "page_count": None, "category": None, "summary": None, "availability": "unknown",
    }
    validate_tool_output(good, _BOOK_OUTPUT_SCHEMA)  # 合法负载通过
    # 额外字段（如成员 ID / 封面 URL）-> 拒绝
    bad = dict(good, member_id=7)
    with pytest.raises(ToolError) as ei:
        validate_tool_output(bad, _BOOK_OUTPUT_SCHEMA)
    assert ei.value.code == "OUTPUT_SCHEMA_MISMATCH"
    # 缺字段 / 类型错误 -> 拒绝
    with pytest.raises(ToolError):
        validate_tool_output({k: v for k, v in good.items() if k != "title"}, _BOOK_OUTPUT_SCHEMA)
    with pytest.raises(ToolError):
        validate_tool_output(dict(good, id="one"), _BOOK_OUTPUT_SCHEMA)


def test_search_output_passes_own_schema(mcp_on, world: dict, db_session: Session,
                                          client: TestClient) -> None:
    """端到端：structuredContent 通过 outputSchema（正常路径不被误伤）。"""
    from app.models import Book
    from app.utils.book_helpers import serialize_json_list
    db_session.add(Book(title="CHK077 契约书", authors=serialize_json_list(["作者"]),
                        category="科幻", language="zh"))
    db_session.commit()
    c = _mcp_client(db_session)
    r = _call(c, world["token"], "bookshelf_search_books", {"query": "契约书"})
    assert r.json()["result"]["isError"] is False
