"""WBS-5：Owner Web 认证 API。

端点：
- POST /auth/login   - Owner 密码登录，设置 HttpOnly Cookie
- POST /auth/logout  - 注销当前会话
- GET  /auth/session - 查询当前会话状态
- POST /auth/init-password - 首次设置 owner 密码（仅未设置时）
- GET  /auth/status  - 公开状态：是否已初始化密码
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.schemas.agent_access import (
    OwnerLoginRequest,
    OwnerLoginResponse,
    OwnerPasswordSetRequest,
    OwnerSessionOut,
)
from app.services import agent_access

router = APIRouter(prefix="/auth", tags=["web-auth"])

_COOKIE_NAME = "hbs_session"
_COOKIE_MAX_AGE = 86400  # 24h


def _set_session_cookie(response: Response, token: str, *, secure: bool = True) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=_COOKIE_NAME, path="/")


def get_session_member(
    request: Request,
    db: Session = Depends(get_db),
) -> "object | None":
    """FastAPI 依赖：从 Cookie 解析当前会话的 Member，无会话返回 None。"""
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    return agent_access.verify_web_session(db, token)


def require_owner(
    request: Request,
    db: Session = Depends(get_db),
):
    """FastAPI 依赖：要求当前会话为 owner，否则 401。"""
    from app.models import Member

    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    member = agent_access.verify_web_session(db, token)
    if member is None:
        raise HTTPException(status_code=401, detail="会话已过期或无效")
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="仅 owner 可执行此操作")
    return member


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    """公开端点：是否已设置 owner 密码。"""
    return {"password_initialized": agent_access.has_owner_password(db)}


def _is_secure_request(request: Request) -> bool:
    """判断请求是否为 HTTPS（或 loopback 测试环境）。"""
    # X-Forwarded-Proto（反向代理）
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto == "https"
    # 直接连接
    return request.url.scheme == "https"


def _is_loopback_request(request: Request) -> bool:
    """判断请求是否来自本机（loopback）。

    GitHub #8：直接对端是可信代理（TRUSTED_PROXIES）时，以 X-Forwarded-For 判定--
    lwa/nginx 反代场景下后端看到的对端是 Docker 网关 IP，真实客户端地址在 XFF 里。
    未配置可信代理时 XFF 一律不可信（防伪造）。

    BUG-181（GitHub #10）：XFF 从右往左解析，跳过可信代理网段后取第一个非可信地址。
    不能取首跳（左值）--按 XFF 语义左值是客户端自报的地址，网关按 nginx 默认
    $proxy_add_x_forwarded_for 追加而非覆盖时，攻击者自带 XFF: 127.0.0.1 即可伪造
    首跳通过 loopback 判定，在 Owner 密码未初始化窗口经 init-password 接管系统。
    右值法下，无论网关追加还是覆盖 XFF，取到的都是网关亲手追加的真实客户端地址。
    """
    client = request.client
    if client is None:
        return False
    host = client.host
    if _is_trusted_proxy(host):
        host = _client_ip_behind_proxy(request) or host
    return host in ("127.0.0.1", "::1", "localhost")


def _client_ip_behind_proxy(request: Request) -> str | None:
    """从 X-Forwarded-For 还原真实客户端 IP：右值法（见 _is_loopback_request 注释）。"""
    xff = request.headers.get("x-forwarded-for", "")
    for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
        if not _is_trusted_proxy(hop):
            return hop
    return None


def _is_trusted_proxy(host: str) -> bool:
    import ipaddress

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in settings.trusted_proxy_networks)


def _normalize_setup_token(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@router.get("/introspect")
def introspect(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Token introspection：验证 Bearer Token 并返回其信息。

    供 CLI `auth status` 和 Agent 自检使用。
    不需要 Owner 会话，但需要有效的 Agent Token。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="需要 Bearer Token")
    bearer = authorization[7:].strip()
    result = agent_access.verify_token(db, bearer)
    if result is None:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    token_row, grant, client, member = result
    scopes = agent_access.get_grant_scopes(grant)
    return {
        "active": True,
        "client_id": client.id,
        "client_name": client.display_name,
        "member_id": member.id,
        "member_name": member.name,
        "scopes": scopes,
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
    }


@router.post("/init-password", response_model=OwnerSessionOut)
def init_password(
    body: OwnerPasswordSetRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_setup_token: str | None = Header(default=None, alias="X-Setup-Token"),
):
    """首次设置 owner 密码。仅当尚未设置时可用。

    安全约束（CHK-039 P1）：
    - 如果系统中已有 owner 成员但尚未设置密码（升级场景），
      必须提供 X-Setup-Token 或从 loopback 访问，防止匿名接管。
    - 如果系统中尚无 owner 成员（全新安装），允许匿名调用
      （set_owner_password 会因无 owner 成员而 400）。
    """
    if agent_access.has_owner_password(db):
        raise HTTPException(status_code=400, detail="Owner 密码已设置，如需重置请使用 CLI 命令")
    if body.password != body.confirm:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")

    # 升级场景：已有 owner 成员但无密码 -> 需要 setup_token 或 loopback
    owner = agent_access.get_owner_member(db)
    if owner is not None:
        expected_token = _normalize_setup_token(settings.setup_token)
        is_loopback = _is_loopback_request(request)
        if expected_token:
            if not x_setup_token or x_setup_token != expected_token:
                raise HTTPException(
                    status_code=403,
                    detail="已有 Owner 成员，初始化密码需要提供正确的 X-Setup-Token",
                )
        elif not is_loopback:
            raise HTTPException(
                status_code=403,
                detail="已有 Owner 成员，初始化密码需要从本机（loopback）访问或配置 SETUP_TOKEN",
            )

    agent_access.set_owner_password(db, body.password)

    # 自动登录
    if owner is None:
        owner = agent_access.get_owner_member(db)
    if owner is None:
        raise HTTPException(status_code=500, detail="Owner 成员不存在")
    token, _ = agent_access.create_web_session(
        db, owner.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, secure=_is_secure_request(request))
    return OwnerSessionOut(authenticated=True, member_id=owner.id, member_name=owner.name)


@router.post("/login", response_model=OwnerLoginResponse)
def login(
    body: OwnerLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Owner 密码登录。"""
    if not agent_access.has_owner_password(db):
        raise HTTPException(status_code=400, detail="Owner 密码尚未设置，请先初始化")
    member = agent_access.verify_owner_password(db, body.password)
    if member is None:
        raise HTTPException(status_code=401, detail="密码错误")
    token, _ = agent_access.create_web_session(
        db, member.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, secure=_is_secure_request(request))
    return OwnerLoginResponse(authenticated=True, member_id=member.id, member_name=member.name)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """注销当前会话。"""
    token = request.cookies.get(_COOKIE_NAME)
    if token:
        agent_access.revoke_web_session(db, token)
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/session", response_model=OwnerSessionOut)
def session_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """查询当前会话状态。"""
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return OwnerSessionOut(authenticated=False)
    member = agent_access.verify_web_session(db, token)
    if member is None:
        return OwnerSessionOut(authenticated=False)
    return OwnerSessionOut(authenticated=True, member_id=member.id, member_name=member.name)
