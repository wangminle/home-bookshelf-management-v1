"""MCP HTTP 协议外壳（WBS-MCP-3）：无状态 JSON-RPC 2.0，路径精确 /mcp。

CHK-073 协议硬化（BUG-196/BUG-195 补充）：
- 协议版本头 MCP-Protocol-Version **必填**且必须在 allowlist（缺头 400）；
- jsonrpc 必须为 "2.0"；params 必须为对象（或省略）——畸形请求稳定 400/-32602，
  不再触发 500；
- Host 必须命中 allowlist（默认仅内置回环精确值；非回环部署显式配置），
  不匹配 421（DNS Rebinding 防护）；带 Origin 时必须精确匹配可信 Origin，否则 403；
- 该协议版本已移除 initialize：握手改用 server/discover（自描述发现）；
  initialize 等未知方法按 -32601 处理。

CHK-073/BUG-198 审计与限流契约：
- 限流键 = Agent Client + Grant + 方法/工具 三维；
- 审计事件携带 request_id / protocol_version / grant_id / grant_version /
  tool_name / scope / data_scope / 参数摘要 / 结果数 / 状态 / 耗时；
- allow 路径审计写入失败 → 503 fail-closed，绝不返回真实数据。

其余不变：默认关闭 404；仅 Bearer（Cookie/渠道头 401）；撤销下一请求生效；
试点 Grant 硬门禁（scopes 恰 {books:read} 且显式 data_scope）。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import db as db_module
from app.config import settings
from app.services import rate_limit, security_audit
from app.mcp_server import tools
from app.mcp_server.auth import (
    MCP_REQUIRED_SCOPE,
    AgentPrincipal,
    build_agent_principal,
    require_pilot_grant,
)

router = APIRouter(redirect_slashes=False)

_JSONRPC_VERSION = "2.0"
_PROTOCOL_VERSION = "2026-07-28"
_SERVER_NAME = "home_bookshelf_mcp"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _server_version() -> str:
    from app.services.agent_discovery import _APP_VERSION

    return _APP_VERSION


def _http_error(status_code: int, code: str, retry_after: int | None = None) -> JSONResponse:
    headers: dict[str, str] = {"X-Error-Code": code}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "data": None, "error": code},
        headers=headers,
    )


def _jsonrpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({
        "jsonrpc": _JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    })


def _host_allowed(request: Request) -> bool:
    host = (request.headers.get("host") or "").strip().lower()
    if not host:
        return False
    # 剥端口：[::1]:8000 → [::1]；a.b.c:8000 → a.b.c
    hostname = (host.split("]")[0] + "]") if host.startswith("[") else host.split(":")[0]
    allowed = _LOOPBACK_HOSTS | settings.mcp_allowed_host_set
    return hostname in allowed or host in allowed


def _origin_allowed(request: Request) -> bool:
    origin = (request.headers.get("origin") or "").strip().lower()
    if not origin:
        return True  # 非浏览器客户端可不带 Origin；带则必须精确匹配
    if origin in settings.mcp_trusted_origin_set:
        return True
    # 默认接受回环 Origin（本机调试客户端）；其余一律拒绝，不接受通配符
    from urllib.parse import urlparse

    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and host in ("localhost", "127.0.0.1", "::1")


def _audit(
    principal: AgentPrincipal | None,
    outcome: str,
    reason: str,
    method: str | None,
    *,
    request_id: str | None = None,
    tool_name: str | None = None,
    args_digest: str | None = None,
    result_count: int | None = None,
    status: int | None = None,
    duration_ms: int | None = None,
    protocol_version: str | None = None,
) -> str:
    details: dict[str, Any] = {"reason": reason, "method": method}
    if request_id is not None:
        details["request_id"] = request_id
    if protocol_version is not None:
        details["protocol_version"] = protocol_version
    if tool_name is not None:
        details["tool_name"] = tool_name
    if args_digest is not None:
        details["args_digest"] = args_digest
    if result_count is not None:
        details["result_count"] = result_count
    if status is not None:
        details["status"] = status
    if duration_ms is not None:
        details["duration_ms"] = duration_ms
    if principal is not None:
        details["grant_id"] = principal.grant_id
        details["grant_version"] = principal.grant_version
        details["scope"] = sorted(principal.scopes)
        details["data_scope"] = principal.data_scope
        subject = f"agent:{principal.agent_client_id}"
    else:
        subject = "anonymous"
    return security_audit.log_security_event(
        event_type="mcp.call",
        outcome=outcome,
        subject=subject,
        details=details,
    )


def _args_digest(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


@router.get("/mcp")
def mcp_get() -> JSONResponse:
    """本试点不提供 GET/SSE 流；仅接受 POST。"""
    if not settings.mcp_enabled:
        return _http_error(404, "NOT_FOUND")
    return _http_error(405, "METHOD_NOT_ALLOWED")


@router.api_route("/mcp/", methods=["GET", "POST"])
def mcp_trailing_slash() -> JSONResponse:
    """精确路径约束：/mcp/ 一律 404，不做 307 重定向（MCP 设计 §13）。"""
    return _http_error(404, "NOT_FOUND")


@router.post("/mcp")
async def mcp_post(
    request: Request,
    mcp_protocol_version: str | None = Header(default=None, alias="MCP-Protocol-Version"),
) -> JSONResponse:
    started = time.monotonic()
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    # 1. 开关与配置门控
    if not settings.mcp_enabled:
        return _http_error(404, "NOT_FOUND")
    if not settings.mcp_cursor_signing_secret:
        return _http_error(500, "CURSOR_SECRET_MISSING")

    # 2. 传输安全：Host allowlist（421）与 Origin 精确匹配（403）
    if not _host_allowed(request):
        return _http_error(421, "HOST_REJECTED")
    if not _origin_allowed(request):
        return _http_error(403, "ORIGIN_REJECTED")

    # 3. 协议版本头必填且在 allowlist（BUG-196：缺头不再放行）
    if mcp_protocol_version is None or not mcp_protocol_version.strip():
        return _http_error(400, "PROTOCOL_VERSION_REQUIRED")
    if mcp_protocol_version.strip() not in settings.mcp_allowed_protocol_version_list:
        return _http_error(400, "PROTOCOL_VERSION_REJECTED")
    protocol_version = mcp_protocol_version.strip()

    # 4. 认证：仅 Bearer；显式拒绝 Cookie/渠道头携带者（MCP 设计 §8.1）
    authorization = request.headers.get("authorization", "")
    if request.cookies.get("hbs_session"):
        _audit(None, "deny", "COOKIE_REJECTED", None, request_id=request_id,
               protocol_version=protocol_version)
        return _http_error(401, "AUTH_REQUIRED")
    if request.headers.get("x-channel") or request.headers.get("x-external-user-id"):
        _audit(None, "deny", "CHANNEL_REJECTED", None, request_id=request_id,
               protocol_version=protocol_version)
        return _http_error(401, "AUTH_REQUIRED")
    bearer = authorization[7:].strip() if authorization.startswith("Bearer ") else None

    # 5. 请求帧校验（BUG-195 补充：畸形请求稳定 400，不触发 500）
    try:
        parsed = await request.json()
    except Exception:
        return _http_error(400, "INVALID_REQUEST")
    if not isinstance(parsed, dict):
        return _http_error(400, "INVALID_REQUEST")
    if parsed.get("jsonrpc") != _JSONRPC_VERSION:
        return _http_error(400, "INVALID_REQUEST")
    method = parsed.get("method")
    if not isinstance(method, str) or not method.strip():
        return _http_error(400, "INVALID_REQUEST")
    params = parsed.get("params")
    if params is not None and not isinstance(params, dict):
        return _http_error(400, "INVALID_REQUEST")
    rpc_id = parsed.get("id")
    if rpc_id is not None and not isinstance(rpc_id, (str, int)):
        return _http_error(400, "INVALID_REQUEST")
    params = params or {}

    with db_module.SessionLocal() as db:
        principal = build_agent_principal(db, bearer)
        if principal is None:
            _audit(None, "deny", "TOKEN_INVALID" if bearer else "AUTH_REQUIRED", method,
                   request_id=request_id, protocol_version=protocol_version)
            return _http_error(401, "TOKEN_INVALID" if bearer else "AUTH_REQUIRED")

        # 6. 限流（BUG-198：Agent Client + Grant + 方法 三维键）
        rl = rate_limit.check(
            f"mcp:{method}:{principal.agent_client_id}:{principal.grant_id}",
            limit=settings.mcp_rate_limit_per_minute,
            window_seconds=60,
        )
        if not rl.allowed:
            _audit(principal, "deny", "RATE_LIMITED", method, request_id=request_id,
                   protocol_version=protocol_version)
            return _http_error(429, "RATE_LIMITED", retry_after=rl.retry_after_seconds)

        # 7. server/discover：自描述发现（该版本已移除 initialize；统一要认证）
        if method == "server/discover":
            audit_state = _audit(principal, "allow", "ok", method, request_id=request_id,
                                 protocol_version=protocol_version, status=200,
                                 duration_ms=int((time.monotonic() - started) * 1000))
            if audit_state == security_audit.AUDIT_FAILED:
                return _http_error(503, "AUDIT_UNAVAILABLE")
            return JSONResponse({
                "jsonrpc": _JSONRPC_VERSION,
                "id": rpc_id,
                "result": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "serverInfo": {"name": _SERVER_NAME, "version": _server_version()},
                    "capabilities": {"tools": {}},
                },
            })

        # 8. 方法分发（initialize 等已移除/未知方法 → -32601；通知静默丢弃）
        if method not in ("tools/list", "tools/call"):
            if rpc_id is None:
                return Response(status_code=202)
            return _jsonrpc_error(rpc_id, -32601, f"Method not found: {method}")

        if MCP_REQUIRED_SCOPE not in principal.scopes:
            _audit(principal, "deny", "SCOPE_DENIED", method, request_id=request_id,
                   protocol_version=protocol_version)
            return _http_error(403, "SCOPE_DENIED")

        # 专用试点 Grant 硬门禁（BUG-197）
        if (
            set(principal.scopes) != {MCP_REQUIRED_SCOPE}
            or principal.data_scope != "household_shared"
        ):
            _audit(principal, "deny", "PILOT_GRANT_REQUIRED", method, request_id=request_id,
                   protocol_version=protocol_version)
            return _http_error(403, "PILOT_GRANT_REQUIRED")

        # 9. tools/list
        if method == "tools/list":
            audit_state = _audit(principal, "allow", "ok", method, request_id=request_id,
                                 protocol_version=protocol_version, status=200,
                                 duration_ms=int((time.monotonic() - started) * 1000))
            if audit_state == security_audit.AUDIT_FAILED:
                return _http_error(503, "AUDIT_UNAVAILABLE")
            return JSONResponse({
                "jsonrpc": _JSONRPC_VERSION,
                "id": rpc_id,
                "result": {"tools": tools.catalog.tool_descriptors()},
            })

        # 10. tools/call
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(tool_name, str) or not tool_name:
            return _jsonrpc_error(rpc_id, -32602, "params.name must be a tool name string")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            # BUG-195 补充：非对象 arguments 稳定 -32602，而非 500
            return _jsonrpc_error(rpc_id, -32602, "params.arguments must be an object")
        if tool_name not in tools.catalog.MCP_TOOL_NAMES:
            _audit(principal, "deny", "TOOL_NOT_FOUND", method, request_id=request_id,
                   protocol_version=protocol_version, tool_name=tool_name,
                   args_digest=_args_digest(arguments))
            return _jsonrpc_error(rpc_id, -32602, f"Unknown tool: {tool_name}")

        # 工具级限流（Client + Grant + Tool 三维，BUG-198）
        rl = rate_limit.check(
            f"mcp:tool:{tool_name}:{principal.agent_client_id}:{principal.grant_id}",
            limit=settings.mcp_rate_limit_per_minute,
            window_seconds=60,
        )
        if not rl.allowed:
            _audit(principal, "deny", "RATE_LIMITED", method, request_id=request_id,
                   protocol_version=protocol_version, tool_name=tool_name,
                   args_digest=_args_digest(arguments))
            return _http_error(429, "RATE_LIMITED", retry_after=rl.retry_after_seconds)

        digest = _args_digest(arguments)
        try:
            tool_result = _call_tool(db, tool_name, arguments)
        except tools.catalog.ToolError as exc:
            _audit(principal, "deny", exc.code, method, request_id=request_id,
                   protocol_version=protocol_version, tool_name=tool_name,
                   args_digest=digest, status=200,
                   duration_ms=int((time.monotonic() - started) * 1000))
            return JSONResponse({
                "jsonrpc": _JSONRPC_VERSION,
                "id": rpc_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": exc.message}],
                    "structuredError": {
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                        "request_id": request_id,
                    },
                },
            })

        # allow 路径审计 fail-closed（BUG-198）：写入失败绝不返回真实数据
        result_count = tool_result.get("count") if isinstance(tool_result, dict) else None
        audit_state = _audit(
            principal, "allow", "ok", method, request_id=request_id,
            protocol_version=protocol_version, tool_name=tool_name,
            args_digest=digest, result_count=result_count, status=200,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        if audit_state == security_audit.AUDIT_FAILED:
            return _http_error(503, "AUDIT_UNAVAILABLE")
        return JSONResponse({
            "jsonrpc": _JSONRPC_VERSION,
            "id": rpc_id,
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}],
                "structuredContent": tool_result,
            },
        })


def _call_tool(db: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "bookshelf_search_books":
        return tools.catalog.search_books(db, arguments)
    if name == "bookshelf_get_book":
        return tools.catalog.get_book(db, arguments)
    raise tools.catalog.ToolError("TOOL_NOT_FOUND", f"Unknown tool: {name}")
