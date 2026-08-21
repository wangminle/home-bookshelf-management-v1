"""WBS-6：统一授权上下文 AuthContext。

替代旧的 channel_headers() + enforce_channel_member() 两段式鉴权。

认证优先级（高→低）：
1. Bearer Token（Agent）→ AuthContext(auth_type="agent", scopes=..., member_id=...)
2. Web Session Cookie（Owner/Member）→ AuthContext(auth_type="web", member_id=..., is_owner=...)
3. Channel Headers（兼容旧渠道）→ AuthContext(auth_type="channel", member_id=...)
4. 匿名 → 401（业务端点不允许匿名）

关键变更（vs 旧 auth.py）：
- 移除 X-UI-Client: web 旁路
- 移除匿名默认成员回退
- 所有业务端点必须通过 Scope 验证
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app import db as db_module
from app.models import Member
from app.services import agent_access, permission_policy
from app.utils.member_helpers import resolve_member_id

# SessionLocal 经模块属性引用（而非 from-import 直接绑定名字）：
# 测试夹具会按用例重绑 app.db.SessionLocal，直接绑定会持有旧引擎引用。

AuthType = Literal["agent", "web", "channel", "anonymous"]


@dataclass
class AuthContext:
    """统一授权上下文。每个业务请求恰好有一个 AuthContext。"""

    auth_type: AuthType
    member_id: int | None = None
    member_name: str | None = None
    member_role: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    # Agent 附加信息
    agent_client_id: int | None = None
    agent_client_name: str | None = None
    grant_id: int | None = None
    # Channel 兼容
    channel: str | None = None
    external_user_id: str | None = None
    # 权限阶段 2：Owner 显式代操作——Web Owner 代表其他成员写入时记录数据归属人
    acting_for_member_id: int | None = None

    @property
    def is_owner(self) -> bool:
        return self.member_role == "owner"

    @property
    def is_authenticated(self) -> bool:
        return self.auth_type in ("agent", "web", "channel")

    def require_scope(self, scope: str) -> None:
        """检查是否拥有指定 scope，否则 403。"""
        if self.auth_type == "web" and self.is_owner:
            # Owner 通过 Web 会话访问时拥有全部 scope
            return
        if scope not in self.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少所需 scope: {scope}",
            )

    def require_any_scope(self, *scopes: str) -> None:
        """检查是否拥有任一 scope。"""
        if self.auth_type == "web" and self.is_owner:
            return
        if not any(s in self.scopes for s in scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少所需 scope（任一）: {', '.join(scopes)}",
            )

    def can_access_member(self, target_member_id: int) -> bool:
        """检查是否可以访问指定成员的数据（scope 级别）。"""
        if self.auth_type == "web" and self.is_owner:
            return True
        if self.member_id == target_member_id:
            return True
        # Agent 需要 stats:household scope 才能跨成员
        if "stats:household" in self.scopes:
            return True
        return False


# ── 旧 auth.py 兼容函数（保留给 members.py 渠道绑定使用） ──

def _normalize_header(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def expected_channel_signature(channel: str, external_user_id: str, secret: str) -> str:
    msg = f"{channel}:{external_user_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _verify_channel_signature(
    channel: str | None,
    external_user_id: str | None,
    signature: str | None,
) -> None:
    secret = _normalize_header(settings.channel_signing_secret)
    if not secret or not (channel and external_user_id):
        return
    if not signature:
        raise HTTPException(
            status_code=403,
            detail="已启用渠道签名校验，请提供 X-Channel-Signature",
        )
    expected = expected_channel_signature(channel, external_user_id, secret)
    if not hmac.compare_digest(signature.strip().lower(), expected):
        raise HTTPException(status_code=403, detail="渠道签名无效")


def require_complete_channel_headers(channel: str | None, external_user_id: str | None) -> None:
    has_channel = bool(channel)
    has_external = bool(external_user_id)
    if has_channel ^ has_external:
        raise HTTPException(
            status_code=400,
            detail="X-Channel 与 X-External-User-Id 必须同时提供或同时省略",
        )


def member_count(db: Session) -> int:
    from sqlalchemy import func
    return db.scalar(select(func.count()).select_from(Member)) or 0


def system_has_channel_bindings(db: Session) -> bool:
    members = db.scalars(select(Member)).all()
    for member in members:
        if not member.channel_bindings:
            continue
        try:
            parsed = json.loads(member.channel_bindings)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and any(
            v is not None and str(v).strip() for v in parsed.values()
        ):
            return True
    return False


def find_members_by_binding(db: Session, channel: str, external_user_id: str) -> list[Member]:
    matched: list[Member] = []
    members = db.scalars(select(Member)).all()
    for member in members:
        if not member.channel_bindings:
            continue
        try:
            parsed = json.loads(member.channel_bindings)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        bound = parsed.get(channel)
        if bound is not None and str(bound) == str(external_user_id):
            matched.append(member)
    return matched


def resolve_member_by_binding(db: Session, channel: str, external_user_id: str) -> Member | None:
    matched = find_members_by_binding(db, channel, external_user_id)
    if not matched:
        return None
    return sorted(matched, key=lambda m: m.id)[0]


def authorize_member_bind(
    db: Session,
    *,
    target_member_id: int,
    channel: str | None,
    external_user_id: str | None,
    setup_token: str | None,
    authorization: str | None = None,
    web_session_token: str | None = None,
) -> None:
    """保护 POST /members/bind。逻辑与旧 auth.py 一致。"""
    # 1. 优先检查 Bearer Token（Agent）
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization[7:].strip()
        result = agent_access.verify_token(db, bearer)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Agent Token 无效或已过期",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token_row, grant, client, member = result
        if member.role == "owner" or member.id == target_member_id:
            return
        raise HTTPException(
            status_code=403,
            detail="只能为自己绑定渠道，或由 owner 代为绑定",
        )

    # 2. Web Session Cookie（Owner/Member Web 登录）
    if web_session_token:
        member = agent_access.verify_web_session(db, web_session_token)
        if member is not None:
            if member.role == "owner" or member.id == target_member_id:
                return
            raise HTTPException(
                status_code=403,
                detail="只能为自己绑定渠道，或由 owner 代为绑定",
            )
        # 无效会话 cookie：继续尝试其他认证方式

    channel = _normalize_header(channel)
    external_user_id = _normalize_header(external_user_id)
    setup_token = _normalize_header(setup_token)
    require_complete_channel_headers(channel, external_user_id)

    expected = _normalize_header(settings.setup_token)
    if expected and setup_token and setup_token == expected:
        return

    if member_count(db) == 0 or not system_has_channel_bindings(db):
        return

    if channel and external_user_id:
        caller = resolve_member_by_binding(db, channel, external_user_id)
        if caller is None:
            raise HTTPException(
                status_code=403,
                detail=f"渠道 {channel} 的外部用户 {external_user_id} 未绑定任何家庭成员，不能执行绑定",
            )
        if caller.id == target_member_id or caller.role == "owner":
            return
        raise HTTPException(status_code=403, detail="只能为自己绑定渠道，或由 owner 代为绑定")

    raise HTTPException(
        status_code=403,
        detail="匿名不可绑定：白名单已建立，请使用已绑定渠道身份或 X-Setup-Token",
    )


# ── AuthContext 构建器 ──

def _build_from_agent_token(
    db: Session,
    bearer: str,
) -> AuthContext | None:
    """从 Bearer Token 构建 Agent AuthContext。"""
    result = agent_access.verify_token(db, bearer)
    if result is None:
        return None
    token_row, grant, client, member = result
    scopes = frozenset(agent_access.get_grant_scopes(grant))
    return AuthContext(
        auth_type="agent",
        member_id=member.id,
        member_name=member.name,
        member_role=member.role,
        scopes=scopes,
        agent_client_id=client.id,
        agent_client_name=client.display_name,
        grant_id=grant.id,
    )


def _build_from_web_session(
    db: Session,
    session_token: str | None,
) -> AuthContext | None:
    """从 Cookie session 构建 Web AuthContext。"""
    if not session_token:
        return None
    member = agent_access.verify_web_session(db, session_token)
    if member is None:
        return None
    return AuthContext(
        auth_type="web",
        member_id=member.id,
        member_name=member.name,
        member_role=member.role,
        # 权限阶段 0：Web 能力集由服务器角色能力表生成（基线 §5.2/§5.3）。
        # Owner 仍在 require_scope 短路；非 Owner 使用 member 能力集
        # （Member 独立登录落地前无成员工据，此路径暂不可达）。
        scopes=permission_policy.role_scopes(member.role),
    )


def _build_from_channel_headers(
    db: Session,
    channel: str | None,
    external_user_id: str | None,
    signature: str | None,
) -> AuthContext | None:
    """从渠道头构建 Channel AuthContext（兼容旧模型）。"""
    channel = _normalize_header(channel)
    external_user_id = _normalize_header(external_user_id)
    if not channel and not external_user_id:
        return None
    require_complete_channel_headers(channel, external_user_id)
    _verify_channel_signature(channel, external_user_id, signature)
    member = resolve_member_by_binding(db, channel, external_user_id)  # type: ignore[arg-type]
    if member is None:
        raise HTTPException(
            status_code=403,
            detail=f"渠道 {channel} 的外部用户 {external_user_id} 未绑定任何家庭成员",
        )
    # BUG-203：停用成员的渠道身份即时失效（与 Web 会话/Agent 口径一致）
    if member.disabled_at is not None:
        raise HTTPException(status_code=403, detail="该成员已停用，无法通过渠道访问")
    # 权限阶段 0（任务 0.5）：修复死分支——渠道能力按绑定 Member 角色映射
    # （基线 §5.4/§8）。此前 owner/member 两分支均给 ALL_SCOPES，非 Owner 渠道
    # 身份实际持有全量能力；现缩权为：member → member 能力集（失去
    # books:delete、stats:household）。这是有意的兼容性缩权（基线 §1.4/§13）。
    scopes = permission_policy.role_scopes(member.role)
    return AuthContext(
        auth_type="channel",
        member_id=member.id,
        member_name=member.name,
        member_role=member.role,
        scopes=scopes,
        channel=channel,
        external_user_id=external_user_id,
    )


# ── FastAPI 依赖 ──

def build_auth_context(
    request: Request,
    authorization: str | None = Header(default=None),
    x_channel: str | None = Header(default=None, alias="X-Channel"),
    x_external_user_id: str | None = Header(default=None, alias="X-External-User-Id"),
    x_channel_signature: str | None = Header(default=None, alias="X-Channel-Signature"),
) -> AuthContext:
    """FastAPI 依赖：构建 AuthContext，按优先级尝试 Agent → Channel → Web → 匿名。

    显式渠道头优先于 Cookie：请求显式声明渠道身份时按渠道解析（不完整/未绑定
    在此报 400/403），避免环境残留的 Web Cookie 静默遮蔽渠道语义。
    如果都不匹配，返回匿名 AuthContext（业务端点应调用 require_auth() 来拒绝）。
    """
    db = db_module.SessionLocal()
    try:
        # 1. Bearer Token (Agent)
        if authorization and authorization.startswith("Bearer "):
            bearer = authorization[7:].strip()
            ctx = _build_from_agent_token(db, bearer)
            if ctx is not None:
                return ctx
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Agent Token 无效或已过期",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 2. Channel Headers（显式渠道身份，优先于环境 Cookie）
        ctx = _build_from_channel_headers(db, x_channel, x_external_user_id, x_channel_signature)
        if ctx is not None:
            return ctx

        # 3. Web Session Cookie
        session_token = request.cookies.get("hbs_session")
        ctx = _build_from_web_session(db, session_token)
        if ctx is not None:
            return ctx

        # 4. 匿名
        return AuthContext(auth_type="anonymous")
    finally:
        db.close()


def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_channel: str | None = Header(default=None, alias="X-Channel"),
    x_external_user_id: str | None = Header(default=None, alias="X-External-User-Id"),
    x_channel_signature: str | None = Header(default=None, alias="X-Channel-Signature"),
) -> AuthContext:
    """FastAPI 依赖：要求认证，匿名返回 401。"""
    ctx = build_auth_context(request, authorization, x_channel, x_external_user_id, x_channel_signature)
    if not ctx.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="此端点要求认证",
        )
    return ctx


def require_scope(scope: str):
    """FastAPI 依赖工厂：要求特定 scope。"""
    def _dep(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
        ctx.require_scope(scope)
        return ctx
    return _dep


def resolve_body_member(
    ctx: AuthContext,
    body_member_id: int | None,
    db: Session | None = None,
) -> int:
    """业务路由辅助：body member_id 与认证身份的一致性校验。

    替代旧 enforce_channel_member 的成员解析段：
    - 未指定 member_id → 认证身份本人；
    - 指定本人 → 放行；
    - Web owner 指定其他成员 → 放行（Web UI 成员切换器以 owner 会话代表家庭成员操作，
      与 can_access_member 的 owner 全量口径一致）；传入 db 时校验成员存在（400）。
      Agent/Channel 即便绑定 owner 成员也不获得代表权——矩阵口径是"绑定成员"；
    - 其他情况指定他人 → 403。
    """
    if ctx.member_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="此端点要求认证",
        )
    if body_member_id is None or body_member_id == ctx.member_id:
        return ctx.member_id
    if ctx.auth_type == "web" and ctx.is_owner:
        if db is not None and db.get(Member, body_member_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"成员 ID {body_member_id} 不存在",
            )
        # 权限阶段 2：显式代操作标记（操作者=ctx.member_id，数据归属人=body_member_id）
        ctx.acting_for_member_id = body_member_id
        return body_member_id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"认证身份绑定的成员 ({ctx.member_id}) 与请求的 member_id ({body_member_id}) 不一致",
    )


# ── CSRF / Origin 校验 ──

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def verify_csrf(request: Request) -> None:
    """CSRF 校验：非安全方法必须来自同源。

    对于 Cookie-based Web 会话，检查 Origin 或 Referer 头是否匹配。
    Bearer Token 和 Channel 请求不受 CSRF 影响（不依赖 Cookie）。
    """
    if request.method in _SAFE_METHODS:
        return

    # 只对 Cookie 会话做 CSRF 检查
    session_token = request.cookies.get("hbs_session")
    if not session_token:
        return  # 非 Cookie 认证，不需要 CSRF

    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少 Origin/Referer 头，疑似 CSRF",
        )

    # 解析 origin，只比较 scheme + host
    from urllib.parse import urlparse
    parsed = urlparse(origin)
    expected_hosts = _get_expected_hosts()
    actual_host = parsed.netloc.lower()
    if actual_host not in expected_hosts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Origin 不匹配: {actual_host}",
        )


def _get_expected_hosts() -> set[str]:
    """获取合法的 host 列表。"""
    hosts: set[str] = set()
    # public_base_url
    if settings.public_base_url:
        from urllib.parse import urlparse
        parsed = urlparse(settings.public_base_url)
        if parsed.netloc:
            hosts.add(parsed.netloc.lower())
    # CORS origins
    if settings.cors_origins != "*":
        for origin in settings.cors_origin_list:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            if parsed.netloc:
                hosts.add(parsed.netloc.lower())
    # loopback
    hosts.update({"localhost", "127.0.0.1", "[::1]"})
    return hosts


# ── 兼容旧 channel_headers 接口 ──
# 旧代码使用 channel_headers() 依赖，我们保留它但内部改为返回兼容对象

@dataclass
class ChannelIdentity:
    """旧版兼容，新代码应直接使用 AuthContext。"""
    member_id: int | None
    channel: str | None
    external_user_id: str | None
    setup_token: str | None = None
    signature: str | None = None
    ui_client: bool = False  # 不再有任何授权含义
    authorization: str | None = None  # Bearer Token（Agent）
    web_session_token: str | None = None  # Web Session Cookie（Owner/Member）


def channel_headers(
    x_channel: str | None = Header(default=None, alias="X-Channel"),
    x_external_user_id: str | None = Header(default=None, alias="X-External-User-Id"),
    x_setup_token: str | None = Header(default=None, alias="X-Setup-Token"),
    x_channel_signature: str | None = Header(default=None, alias="X-Channel-Signature"),
    x_ui_client: str | None = Header(default=None, alias="X-UI-Client"),
    authorization: str | None = Header(default=None),
    hbs_session: str | None = Cookie(default=None, alias="hbs_session"),
) -> ChannelIdentity:
    """旧版兼容依赖。新代码应使用 build_auth_context / require_auth。"""
    channel = _normalize_header(x_channel)
    external_user_id = _normalize_header(x_external_user_id)
    signature = _normalize_header(x_channel_signature)
    _verify_channel_signature(channel, external_user_id, signature)
    # X-UI-Client 不再具有任何授权含义
    return ChannelIdentity(
        member_id=None,
        channel=channel,
        external_user_id=external_user_id,
        setup_token=_normalize_header(x_setup_token),
        signature=signature,
        ui_client=False,  # 始终 False，不再有旁路
        authorization=authorization,
        web_session_token=hbs_session,
    )


def enforce_channel_member(
    db: Session,
    *,
    body_member_id: int | None,
    channel: str | None,
    external_user_id: str | None,
    require_channel: bool = False,
    ui_client: bool = False,
    authorization: str | None = None,
    required_scope: str | None = None,
    web_session_token: str | None = None,
) -> int:
    """旧版兼容函数。新代码应使用 AuthContext。

    注意：ui_client 参数不再有任何效果。所有请求必须通过渠道身份、Web 会话或 Agent Token 认证。
    """
    # 1. 优先检查 Bearer Token（Agent）
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization[7:].strip()
        result = agent_access.verify_token(db, bearer)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Agent Token 无效或已过期",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token_row, grant, client, member = result
        if body_member_id is not None and body_member_id != member.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Agent Token 绑定的成员 ({member.id}) 与请求的 member_id ({body_member_id}) 不一致",
            )
        # Scope 检查
        if required_scope is not None:
            grant_scopes = agent_access.get_grant_scopes(grant)
            if required_scope not in grant_scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"缺少所需 scope: {required_scope}",
                )
        return member.id

    # 2. Web Session Cookie（Owner/Member Web 登录）
    if web_session_token:
        member = agent_access.verify_web_session(db, web_session_token)
        if member is not None:
            if body_member_id is not None and body_member_id != member.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Web 会话绑定的成员 ({member.id}) 与请求的 member_id ({body_member_id}) 不一致",
                )
            # Web 会话不检查 scope（Owner 拥有全部权限，非 Owner 后续可细化）
            return member.id
        # 无效会话 cookie：继续尝试其他认证方式

    channel = _normalize_header(channel)
    external_user_id = _normalize_header(external_user_id)
    require_complete_channel_headers(channel, external_user_id)

    if not channel and not external_user_id:
        # 不再允许匿名回退（除非系统完全未初始化）
        bindings_established = system_has_channel_bindings(db)
        if require_channel or bindings_established:
            raise HTTPException(
                status_code=403,
                detail="此端点要求认证，请提供渠道身份、Web 会话或 Agent Token",
            )
        # 系统尚未建立任何绑定，允许初始化
        try:
            return resolve_member_id(db, body_member_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    member = resolve_member_by_binding(db, channel, external_user_id)  # type: ignore[arg-type]
    if member is None:
        raise HTTPException(
            status_code=403,
            detail=f"渠道 {channel} 的外部用户 {external_user_id} 未绑定任何家庭成员",
        )
    # BUG-203：停用成员的渠道身份即时失效（与 Web 会话/Agent 口径一致）
    if member.disabled_at is not None:
        raise HTTPException(status_code=403, detail="该成员已停用，无法通过渠道访问")
    if body_member_id is not None and body_member_id != member.id:
        raise HTTPException(
            status_code=403,
            detail=f"渠道身份与指定 member_id 不一致（渠道绑定 {member.id}，请求 {body_member_id}）",
        )
    return member.id
