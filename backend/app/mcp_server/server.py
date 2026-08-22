"""MCP HTTP 协议外壳（WBS-MCP-3）：无状态 JSON-RPC 2.0，路径精确 /mcp。

CHK-073 协议硬化（BUG-196/BUG-195 补充）：
- 协议版本头 MCP-Protocol-Version **必填**且必须在 allowlist（缺头 400）；
- jsonrpc 必须为 "2.0"；params 必须为对象且携带 `_meta` 对象（每请求自
  描述元数据，CHK-077/BUG-208：2026-07-28 无状态契约）--畸形请求稳定
  400/-32602，不再触发 500；
- 网关路由头 Mcp-Method/Mcp-Name 与请求体一致性校验（BUG-208）：不一致
  稳定 400；Mcp-Name 仅适用于命名方法（tools/call），不强制 tools/list；
- Host 必须命中 allowlist（默认仅内置回环精确值；非回环部署显式配置），
  不匹配 421（DNS Rebinding 防护）；带 Origin 时必须精确匹配可信 Origin，否则 403；
- 该协议版本已移除 initialize：握手改用 server/discover（自描述发现，
  返回 supportedVersions/resultType，serverInfo/capabilities 在 result._meta）；
  initialize 等未知方法按 -32601 处理。

CHK-073/BUG-198 审计与限流契约：
- 限流两层（CHK-077/BUG-211）：请求级 = Agent Client + Grant（方法进入键
  之前先过全局预算，未知方法同样消耗配额，攻击者可控的方法名不进任何键）；
  工具级 = Client + Grant + Tool（工具名先过 allowlist）；
- 审计事件携带 request_id / protocol_version / client_info / agent_client_id /
  token_prefix（非明文）/ grant_id / grant_version / tool_name / scope /
  data_scope / 参数摘要 / 结果数 / 状态 / 耗时 / source_ip /
  trusted_proxy_result（BUG-216）；allow 与 deny 的抑制键按 method/tool
  分维度（BUG-209：discover 放行不再抑制工具调用审计）；tools/call 放行
  逐次审计（suppress_seconds=0，真实数据读取不留采样空洞）；
- allow 路径审计写入失败 -> 503 fail-closed，绝不返回真实数据。

CHK-077/BUG-212 传输与输入硬上限：
- 请求体超过 mcp_max_request_body_bytes -> 413（Content-Length 预检 +
  流式累计双保险，无 Content-Length 的分块请求同样受限）；
- 响应体超限防御性拒绝 500（页长契约保证正常响应远小于该值）；
- 游标签名密钥必须 >= 32 字符且不得复用渠道签名密钥/初始化令牌（500）。

CHK-077/BUG-214 可信网络部署档：
- 默认仅回环可信；非回环来源（直连或可信代理还原后的真实客户端）必须
  命中 MCP_TRUSTED_CIDRS（与匿名书架 TRUSTED_LAN_CIDRS 相互独立）；
- MCP_REQUIRE_HTTPS 默认 true：HTTP 仅限回环；家庭局域网 HTTP 试点须
  Owner 显式 false 且配置 CIDR；可信代理后的真实 scheme 以
  X-Forwarded-Proto 首值判定，缺失即拒绝（fail-closed）。

CHK-077/BUG-215 错误兜底：
- 数据库异常（含 busy/超时）-> 稳定可重试工具错误 + 完整调用审计；
- 其他未捕获异常 -> JSON-RPC -32603（retryable=true）+ 审计，不再裸 500。

其余不变：默认关闭 404；仅 Bearer（Cookie/渠道头 401）；撤销下一请求生效；
试点 Grant 硬门禁（scopes 恰 {books:read} 且显式 data_scope）。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import db as db_module
from app.config import settings
from app.services import rate_limit, security_audit
from app.services.trusted_network import TrustDecision, evaluate_trust
from app.mcp_server import tools
from app.mcp_server.auth import (
    MCP_REQUIRED_SCOPE,
    AgentPrincipal,
    build_agent_principal,
)

logger = logging.getLogger("mcp_server")

router = APIRouter(redirect_slashes=False)

_JSONRPC_VERSION = "2.0"
_SERVER_NAME = "home_bookshelf_mcp"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
# 方法名/头长度硬上限（BUG-211：攻击者可控字符串进入审计/错误前先限长）
_METHOD_MAX_LENGTH = 128


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


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    retryable: bool | None = None,
    mcp_request_id: str | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    data: dict[str, Any] = {}
    if retryable is not None:
        data["retryable"] = retryable
    if mcp_request_id is not None:
        data["request_id"] = mcp_request_id
    if data:
        error["data"] = data
    return JSONResponse({
        "jsonrpc": _JSONRPC_VERSION,
        "id": request_id,
        "error": error,
    })


def _host_allowed(request: Request) -> bool:
    host = (request.headers.get("host") or "").strip().lower()
    if not host:
        return False
    # 剥端口：[::1]:8000 -> [::1]；a.b.c:8000 -> a.b.c
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


# ── 可信网络与 HTTPS 门禁（CHK-077/BUG-214，MCP 设计 §13） ──

def _evaluate_network(request: Request) -> TrustDecision:
    """按 MCP 专用 CIDR 判定来源可信度（与匿名书架的 LAN CIDR 相互独立）。"""
    peer = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for") or ""
    return evaluate_trust(
        peer,
        xff,
        trusted_lan_networks=settings.mcp_trusted_cidr_networks,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )


def _is_loopback_ip(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def _https_allowed(request: Request, trust: TrustDecision) -> bool:
    """HTTPS 档：默认仅回环可 HTTP；其余来源必须 https（可信代理看 XFP 首值）。"""
    if not settings.mcp_require_https:
        return True
    if _is_loopback_ip(trust.client_ip):
        return True  # 回环豁免（本机调试）
    scheme = request.url.scheme
    if trust.reason.startswith("proxy_"):
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if not forwarded_proto:
            return False  # 可信代理未声明真实 scheme -> fail-closed
        scheme = forwarded_proto
    return scheme == "https"


# ── 游标密钥与请求体硬上限（CHK-077/BUG-212） ──

def _cursor_secret_valid() -> tuple[bool, str]:
    """游标签名密钥质量门：非空、>= 32 字符、不复用其他密钥。"""
    secret = settings.mcp_cursor_signing_secret
    if not secret:
        return False, "CURSOR_SECRET_MISSING"
    if len(secret) < 32:
        return False, "CURSOR_SECRET_INVALID"
    if settings.channel_signing_secret and secret == settings.channel_signing_secret:
        return False, "CURSOR_SECRET_INVALID"
    if settings.setup_token and secret == settings.setup_token:
        return False, "CURSOR_SECRET_INVALID"
    return True, ""


async def _read_capped_body(request: Request) -> bytes | None:
    """读取请求体并施加硬上限；超限返回 None（调用方回 413）。

    Content-Length 预检 + 流式累计双保险：无 Content-Length 的分块传输
    同样受限，不会整体载入内存后再判断。
    """
    declared = (request.headers.get("content-length") or "").strip()
    if declared:
        if not declared.isdigit() or int(declared) > settings.mcp_max_request_body_bytes:
            return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > settings.mcp_max_request_body_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


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
    client_ip: str | None = None,
    trust_reason: str | None = None,
) -> str:
    # interface 维度：与 REST/Public Catalog 共用同一审计存储时区分入口（MCP 第二期清单第 4 点）
    details: dict[str, Any] = {"interface": "mcp", "reason": reason, "method": method}
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
    # BUG-216：来源与客户端身份字段（token_prefix 为非明文前缀，可关联令牌记录）
    if client_ip is not None:
        details["source_ip"] = client_ip
    if trust_reason is not None:
        details["trusted_proxy_result"] = trust_reason
    if principal is not None:
        details["grant_id"] = principal.grant_id
        details["grant_version"] = principal.grant_version
        details["agent_client_id"] = principal.agent_client_id
        details["client_info"] = {
            "name": principal.agent_client_name,
            "type": principal.agent_client_type,
        }
        details["token_prefix"] = principal.token_prefix
        details["scope"] = sorted(principal.scopes)
        details["data_scope"] = principal.data_scope
        subject = f"agent:{principal.agent_client_id}"
    else:
        subject = "anonymous"
    # BUG-209：抑制键带 method/tool 维度（discover 放行不再抑制工具调用审计）；
    # tools/call 放行逐次审计（真实数据读取不留采样空洞）
    suppress_seconds = 0 if (outcome == "allow" and method == "tools/call") else None
    return security_audit.log_security_event(
        event_type="mcp.call",
        outcome=outcome,
        subject=subject,
        details=details,
        suppress_seconds=suppress_seconds,
        suppress_key=("mcp.call", outcome, subject, method or "", tool_name or ""),
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
    mcp_method_header: str | None = Header(default=None, alias="Mcp-Method"),
    mcp_name_header: str | None = Header(default=None, alias="Mcp-Name"),
) -> JSONResponse:
    started = time.monotonic()
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    # 1. 开关与配置门控
    if not settings.mcp_enabled:
        return _http_error(404, "NOT_FOUND")
    secret_ok, secret_error = _cursor_secret_valid()
    if not secret_ok:
        return _http_error(500, secret_error)

    # 2. 传输安全：Host allowlist（421）与 Origin 精确匹配（403）
    if not _host_allowed(request):
        return _http_error(421, "HOST_REJECTED")
    if not _origin_allowed(request):
        return _http_error(403, "ORIGIN_REJECTED")

    # 3. 可信网络与 HTTPS 档（BUG-214：非回环来源须命中 MCP_TRUSTED_CIDRS；
    #    HTTPS 默认强制，回环豁免；判定用还原后的真实客户端 IP）
    trust = _evaluate_network(request)
    if not trust.trusted:
        _audit(None, "deny", "NETWORK_DENIED", None, request_id=request_id,
               client_ip=trust.client_ip, trust_reason=trust.reason)
        return _http_error(403, "NETWORK_DENIED")
    if not _https_allowed(request, trust):
        _audit(None, "deny", "HTTPS_REQUIRED", None, request_id=request_id,
               client_ip=trust.client_ip, trust_reason=trust.reason)
        return _http_error(403, "HTTPS_REQUIRED")

    # 4. 协议版本头必填且在 allowlist（BUG-196：缺头不再放行）
    if mcp_protocol_version is None or not mcp_protocol_version.strip():
        return _http_error(400, "PROTOCOL_VERSION_REQUIRED")
    if mcp_protocol_version.strip() not in settings.mcp_allowed_protocol_version_list:
        return _http_error(400, "PROTOCOL_VERSION_REJECTED")
    protocol_version = mcp_protocol_version.strip()

    # 5. 认证：仅 Bearer；显式拒绝 Cookie/渠道头携带者（MCP 设计 §8.1）
    authorization = request.headers.get("authorization", "")
    if request.cookies.get("hbs_session"):
        _audit(None, "deny", "COOKIE_REJECTED", None, request_id=request_id,
               protocol_version=protocol_version, client_ip=trust.client_ip,
               trust_reason=trust.reason)
        return _http_error(401, "AUTH_REQUIRED")
    if request.headers.get("x-channel") or request.headers.get("x-external-user-id"):
        _audit(None, "deny", "CHANNEL_REJECTED", None, request_id=request_id,
               protocol_version=protocol_version, client_ip=trust.client_ip,
               trust_reason=trust.reason)
        return _http_error(401, "AUTH_REQUIRED")
    bearer = authorization[7:].strip() if authorization.startswith("Bearer ") else None

    # 6. 请求体硬上限（BUG-212：超限 413，不整体载入内存）
    body = await _read_capped_body(request)
    if body is None:
        return _http_error(413, "REQUEST_TOO_LARGE")

    # 7. 请求帧校验（BUG-195/208：畸形请求稳定 400；params 必须携带 _meta 对象）
    try:
        parsed = json.loads(body)
    except Exception:
        return _http_error(400, "INVALID_REQUEST")
    if not isinstance(parsed, dict):
        return _http_error(400, "INVALID_REQUEST")
    if parsed.get("jsonrpc") != _JSONRPC_VERSION:
        return _http_error(400, "INVALID_REQUEST")
    method = parsed.get("method")
    if not isinstance(method, str) or not method.strip():
        return _http_error(400, "INVALID_REQUEST")
    if len(method) > _METHOD_MAX_LENGTH:
        return _http_error(400, "INVALID_REQUEST")
    params = parsed.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("_meta"), dict):
        # BUG-208：2026-07-28 无状态契约要求每请求自带元数据
        return _http_error(400, "PARAMS_META_REQUIRED")
    rpc_id = parsed.get("id")
    if rpc_id is not None and not isinstance(rpc_id, (str, int)):
        return _http_error(400, "INVALID_REQUEST")

    # 8. 网关路由头一致性（BUG-208：Mcp-Method/Mcp-Name 与请求体不符 -> 400；
    #    Mcp-Name 仅适用于命名方法 tools/call，不强制 tools/list）
    if mcp_method_header is not None and mcp_method_header.strip() != method:
        return _http_error(400, "HEADER_BODY_MISMATCH")
    if mcp_name_header is not None:
        if method != "tools/call":
            return _http_error(400, "HEADER_BODY_MISMATCH")
        name_param = params.get("name")
        if not isinstance(name_param, str) or mcp_name_header.strip() != name_param:
            return _http_error(400, "HEADER_BODY_MISMATCH")

    principal: AgentPrincipal | None = None
    with db_module.SessionLocal() as db:
        try:
            principal = build_agent_principal(db, bearer)
            if principal is None:
                _audit(None, "deny", "TOKEN_INVALID" if bearer else "AUTH_REQUIRED", method,
                       request_id=request_id, protocol_version=protocol_version,
                       client_ip=trust.client_ip, trust_reason=trust.reason)
                return _http_error(401, "TOKEN_INVALID" if bearer else "AUTH_REQUIRED")

            # 9. 请求级限流（BUG-211：Client + Grant 全局预算，先于方法 allowlist--
            #    未知方法同样消耗配额；攻击者可控的方法名不进入任何限流键）
            rl = rate_limit.check(
                f"mcp:req:{principal.agent_client_id}:{principal.grant_id}",
                limit=settings.mcp_rate_limit_per_minute,
                window_seconds=60,
            )
            if not rl.allowed:
                _audit(principal, "deny", "RATE_LIMITED", method, request_id=request_id,
                       protocol_version=protocol_version, client_ip=trust.client_ip,
                       trust_reason=trust.reason)
                return _http_error(429, "RATE_LIMITED", retry_after=rl.retry_after_seconds)

            # 10. server/discover：自描述发现（该版本已移除 initialize；统一要认证）
            if method == "server/discover":
                audit_state = _audit(principal, "allow", "ok", method, request_id=request_id,
                                     protocol_version=protocol_version, status=200,
                                     duration_ms=int((time.monotonic() - started) * 1000),
                                     client_ip=trust.client_ip, trust_reason=trust.reason)
                if audit_state == security_audit.AUDIT_FAILED:
                    return _http_error(503, "AUDIT_UNAVAILABLE")
                # BUG-208：DiscoverResult 契约 = supportedVersions + resultType，
                # serverInfo/capabilities 在 result._meta（不再自定义 protocolVersion）
                return JSONResponse({
                    "jsonrpc": _JSONRPC_VERSION,
                    "id": rpc_id,
                    "result": {
                        "supportedVersions": list(settings.mcp_allowed_protocol_version_list),
                        "resultType": "discover",
                        "_meta": {
                            "serverInfo": {"name": _SERVER_NAME, "version": _server_version()},
                            "capabilities": {"tools": {}},
                        },
                    },
                })

            # 11. 方法分发（initialize 等已移除/未知方法 -> -32601；通知静默丢弃）
            if method not in ("tools/list", "tools/call"):
                if rpc_id is None:
                    return Response(status_code=202)
                return _jsonrpc_error(rpc_id, -32601, f"Method not found: {method[:64]}")

            if MCP_REQUIRED_SCOPE not in principal.scopes:
                _audit(principal, "deny", "SCOPE_DENIED", method, request_id=request_id,
                       protocol_version=protocol_version, client_ip=trust.client_ip,
                       trust_reason=trust.reason)
                return _http_error(403, "SCOPE_DENIED")

            # 专用试点 Grant 硬门禁（BUG-197）
            if (
                set(principal.scopes) != {MCP_REQUIRED_SCOPE}
                or principal.data_scope != "household_shared"
            ):
                _audit(principal, "deny", "PILOT_GRANT_REQUIRED", method, request_id=request_id,
                       protocol_version=protocol_version, client_ip=trust.client_ip,
                       trust_reason=trust.reason)
                return _http_error(403, "PILOT_GRANT_REQUIRED")

            # 12. tools/list
            if method == "tools/list":
                audit_state = _audit(principal, "allow", "ok", method, request_id=request_id,
                                     protocol_version=protocol_version, status=200,
                                     duration_ms=int((time.monotonic() - started) * 1000),
                                     client_ip=trust.client_ip, trust_reason=trust.reason)
                if audit_state == security_audit.AUDIT_FAILED:
                    return _http_error(503, "AUDIT_UNAVAILABLE")
                return JSONResponse({
                    "jsonrpc": _JSONRPC_VERSION,
                    "id": rpc_id,
                    "result": {"tools": tools.catalog.tool_descriptors()},
                })

            # 13. tools/call
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
                       protocol_version=protocol_version, tool_name=tool_name[:64],
                       args_digest=_args_digest(arguments), client_ip=trust.client_ip,
                       trust_reason=trust.reason)
                return _jsonrpc_error(rpc_id, -32602, f"Unknown tool: {tool_name[:64]}")

            # 工具级限流（Client + Grant + Tool 三维，BUG-198/211：工具名已过 allowlist）
            rl = rate_limit.check(
                f"mcp:tool:{tool_name}:{principal.agent_client_id}:{principal.grant_id}",
                limit=settings.mcp_rate_limit_per_minute,
                window_seconds=60,
            )
            if not rl.allowed:
                _audit(principal, "deny", "RATE_LIMITED", method, request_id=request_id,
                       protocol_version=protocol_version, tool_name=tool_name,
                       args_digest=_args_digest(arguments), client_ip=trust.client_ip,
                       trust_reason=trust.reason)
                return _http_error(429, "RATE_LIMITED", retry_after=rl.retry_after_seconds)

            digest = _args_digest(arguments)
            try:
                tool_result = _call_tool(db, tool_name, arguments)
            except tools.catalog.ToolError as exc:
                _audit(principal, "deny", exc.code, method, request_id=request_id,
                       protocol_version=protocol_version, tool_name=tool_name,
                       args_digest=digest, status=200,
                       duration_ms=int((time.monotonic() - started) * 1000),
                       client_ip=trust.client_ip, trust_reason=trust.reason)
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
            except SQLAlchemyError:
                # BUG-215：数据库异常（busy/超时/约束等）映射为稳定可重试错误 +
                # 完整调用审计，不泄露 SQL/堆栈，不再裸 500
                db.rollback()
                logger.exception("mcp tool db error: %s %s", method, request_id)
                _audit(principal, "deny", "DB_BUSY", method, request_id=request_id,
                       protocol_version=protocol_version, tool_name=tool_name,
                       args_digest=digest, status=200,
                       duration_ms=int((time.monotonic() - started) * 1000),
                       client_ip=trust.client_ip, trust_reason=trust.reason)
                return JSONResponse({
                    "jsonrpc": _JSONRPC_VERSION,
                    "id": rpc_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "数据库暂时不可用，请稍后重试"}],
                        "structuredError": {
                            "code": "DB_BUSY",
                            "message": "数据库暂时不可用，请稍后重试",
                            "retryable": True,
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
                client_ip=trust.client_ip, trust_reason=trust.reason,
            )
            if audit_state == security_audit.AUDIT_FAILED:
                return _http_error(503, "AUDIT_UNAVAILABLE")
            response = JSONResponse({
                "jsonrpc": _JSONRPC_VERSION,
                "id": rpc_id,
                "result": {
                    "isError": False,
                    "content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}],
                    "structuredContent": tool_result,
                },
            })
            # BUG-212：响应体防御性硬上限（页长契约保证正常响应远小于该值）
            if len(response.body) > settings.mcp_max_response_body_bytes:
                _audit(principal, "deny", "RESPONSE_TOO_LARGE", method, request_id=request_id,
                       protocol_version=protocol_version, tool_name=tool_name,
                       args_digest=digest, status=500,
                       duration_ms=int((time.monotonic() - started) * 1000),
                       client_ip=trust.client_ip, trust_reason=trust.reason)
                return _http_error(500, "RESPONSE_TOO_LARGE")
            return response

        except Exception:  # noqa: BLE001 - BUG-215：兜底稳定 -32603 + 审计，不裸 500
            logger.exception("mcp internal error: %s %s", method, request_id)
            _audit(principal, "deny", "INTERNAL_ERROR", method, request_id=request_id,
                   protocol_version=protocol_version, status=500,
                   duration_ms=int((time.monotonic() - started) * 1000),
                   client_ip=trust.client_ip, trust_reason=trust.reason)
            return _jsonrpc_error(
                rpc_id, -32603, "内部错误，请稍后重试",
                retryable=True, mcp_request_id=request_id,
            )


def _call_tool(db: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "bookshelf_search_books":
        return tools.catalog.search_books(db, arguments)
    if name == "bookshelf_get_book":
        return tools.catalog.get_book(db, arguments)
    raise tools.catalog.ToolError("TOOL_NOT_FOUND", f"Unknown tool: {name}")
