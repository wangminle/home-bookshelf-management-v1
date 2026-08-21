from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_context import (
    AuthContext,
    build_auth_context,
    require_scope,
    verify_csrf,
)
from app.auth import (
    ChannelIdentity,
    authorize_member_bind,
    channel_headers,
    system_has_channel_bindings,
)
from app.db import get_db
from app.models import Member
from app.schemas.book import ApiResponse
from app.schemas.member import (
    MemberBind,
    MemberCreate,
    MemberOut,
    MemberPasswordSetRequest,
    MemberUpdateRequest,
)
from app.services.members import bind_member_channel, create_member, list_members
from app.services import agent_access
from app.api.v1.web_auth import require_owner
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit
from app.utils.serializers import member_bindings

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=ApiResponse)
def get_members(
    ctx: AuthContext = Depends(require_scope("members:read")),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """BUG-167：接 members:read 鉴权。channel_bindings 仅 owner 可见（BUG-113）。"""
    members = list_members(db)
    show_bindings = ctx.is_owner
    items = [
        MemberOut(
            id=m.id,
            name=m.name,
            role=m.role,
            username=m.username,
            disabled_at=m.disabled_at,
            avatar_path=m.avatar_path,
            channel_bindings=(member_bindings(m.channel_bindings) if show_bindings else None),
            reading_streak_offset=m.reading_streak_offset,
            created_at=m.created_at,
            updated_at=m.updated_at,
        ).model_dump()
        for m in members
    ]
    return ApiResponse(data={"items": items, "total": len(items)})


@router.post("", response_model=ApiResponse, status_code=201)
def add_member(
    payload: MemberCreate,
    ctx: AuthContext = Depends(build_auth_context),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """创建成员（BUG-168：改走 AuthContext）。

    引导期（尚无任何渠道绑定）：允许匿名创建成员，与 README 先 member 后 bind 流程兼容；
    白名单建立后：要求 owner 身份（Web 会话/Agent Token/已绑定 owner 渠道）。
    """
    caller_role: str | None = None
    if ctx.is_authenticated:
        if ctx.member_role != "owner" and system_has_channel_bindings(db):
            raise HTTPException(
                status_code=403,
                detail="只有 owner 角色的成员可以创建成员（白名单已建立）",
            )
        caller_role = ctx.member_role
    else:
        # 匿名：仅引导期放行
        if system_has_channel_bindings(db):
            raise HTTPException(
                status_code=403,
                detail="白名单已建立，请使用已绑定渠道身份创建成员",
            )

    # BUG-112：只有 owner 可创建 owner，防止 guest/member 自行提权
    if payload.role == "owner" and caller_role != "owner":
        # 引导期（无任何绑定）仍允许创建首个 owner
        if system_has_channel_bindings(db):
            raise HTTPException(
                status_code=403,
                detail="只有 owner 角色的成员可以创建新的 owner",
            )

    try:
        result = create_member(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    member = result.member
    log_and_commit(
        db,
        action="member.create",
        channel=ctx.channel,
        payload={"member_id": member.id, "name": member.name},
    )
    data = MemberOut(
        id=member.id,
        name=member.name,
        role=member.role,
        username=member.username,
        disabled_at=member.disabled_at,
        avatar_path=member.avatar_path,
        channel_bindings=member_bindings(member.channel_bindings),
        reading_streak_offset=member.reading_streak_offset,
        created_at=member.created_at,
        updated_at=member.updated_at,
    ).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)


@router.post("/bind", response_model=ApiResponse)
def bind_channel(
    payload: MemberBind,
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    authorize_member_bind(
        db,
        target_member_id=payload.member_id,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
        authorization=identity.authorization,
        web_session_token=identity.web_session_token,
        setup_token=identity.setup_token,
    )
    try:
        result = bind_member_channel(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    member = result.member
    log_and_commit(
        db,
        action="member.bind",
        member_id=member.id,
        channel=payload.channel,
        payload={"external_user_id": payload.external_user_id},
    )
    data = MemberOut(
        id=member.id,
        name=member.name,
        role=member.role,
        avatar_path=member.avatar_path,
        channel_bindings=member_bindings(member.channel_bindings),
        reading_streak_offset=member.reading_streak_offset,
        created_at=member.created_at,
        updated_at=member.updated_at,
    ).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)


@router.patch("/{member_id}", response_model=ApiResponse)
def update_member(
    member_id: int,
    payload: MemberUpdateRequest,
    db: Session = Depends(get_db),
    owner=Depends(require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """Owner 成员管理（权限阶段 2）：角色调整与停用/恢复。

    - 末位活跃 owner 保护：不允许把唯一活跃 owner 降级或停用；
    - 角色变化或停用后，该成员全部 Web 会话立即失效（基线 §5.2）。
    """
    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")

    changed: dict = {}
    if payload.role is not None and payload.role != member.role:
        if member.role == "owner" and payload.role != "owner":
            _guard_last_active_owner(db, member)
        member.role = payload.role
        changed["role"] = payload.role
        agent_access.revoke_member_sessions(db, member.id)
    if payload.disabled is not None:
        if payload.disabled:
            if member.role == "owner":
                _guard_last_active_owner(db, member)
            member.disabled_at = member.disabled_at or datetime.now(timezone.utc).replace(tzinfo=None)
            changed["disabled"] = True
            agent_access.revoke_member_sessions(db, member.id)
        else:
            member.disabled_at = None
            changed["disabled"] = False

    if changed:
        db.commit()
        db.refresh(member)
    log_and_commit(
        db,
        action="member.update",
        member_id=member.id,
        payload={"changes": changed, "operator_member_id": owner.id},
    )
    data = MemberOut(
        id=member.id, name=member.name, role=member.role,
        username=member.username, disabled_at=member.disabled_at,
        avatar_path=member.avatar_path,
        channel_bindings=(member_bindings(member.channel_bindings) if owner.role == "owner" else None),
        reading_streak_offset=member.reading_streak_offset,
        created_at=member.created_at, updated_at=member.updated_at,
    ).model_dump()
    data["message"] = "成员已更新" if changed else "无变更"
    return ApiResponse(data=data)


def _guard_last_active_owner(db: Session, member: Member) -> None:
    """降级/停用 owner 前，确认仍有其它活跃 owner。"""
    from sqlalchemy import select as sa_select

    active_owners = [
        m for m in db.scalars(sa_select(Member).where(Member.role == "owner")).all()
        if m.id != member.id and m.disabled_at is None
    ]
    if not active_owners:
        raise HTTPException(status_code=400, detail="不能停用或降级唯一的活跃 owner")


@router.post("/{member_id}/password", response_model=ApiResponse)
def reset_member_password(
    member_id: int,
    payload: MemberPasswordSetRequest,
    db: Session = Depends(get_db),
    owner=Depends(require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """Owner 重置成员密码（权限阶段 2）：设置新密码并撤销该成员全部会话。"""
    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if member.disabled_at is not None:
        raise HTTPException(status_code=400, detail="成员已停用，请先恢复再设置密码")
    agent_access.set_member_password(db, member, payload.password)
    revoked = agent_access.revoke_member_sessions(db, member.id)
    if not member.username:
        member.username = agent_access.ensure_unique_username(db, member.name or "user", exclude_id=member.id)
        db.commit()
    log_and_commit(
        db,
        action="member.password_reset",
        member_id=member.id,
        payload={"operator_member_id": owner.id, "revoked_sessions": revoked},
    )
    return ApiResponse(data={"member_id": member.id, "username": member.username, "revoked_sessions": revoked})
