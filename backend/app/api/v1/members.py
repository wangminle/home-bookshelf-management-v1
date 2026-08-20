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
from app.schemas.member import MemberBind, MemberCreate, MemberOut
from app.services.members import bind_member_channel, create_member, list_members
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