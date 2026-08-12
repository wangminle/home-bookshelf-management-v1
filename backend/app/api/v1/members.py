from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import (
    ChannelIdentity,
    authorize_member_bind,
    channel_headers,
    enforce_channel_member,
    resolve_member_by_binding,
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
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    members = list_members(db)
    # BUG-113：匿名请求不返回 channel_bindings，防止复制 owner 外部身份冒充
    caller_member = None
    if identity.channel and identity.external_user_id:
        caller_member = resolve_member_by_binding(db, identity.channel, identity.external_user_id)
    show_bindings = caller_member is not None and caller_member.role == "owner"
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
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    # 引导期（尚无任何渠道绑定）：允许匿名创建成员，与 README 先 member 后 bind 流程兼容；
    # 白名单建立后：必须有渠道身份，防止匿名无限创建成员。
    # 带渠道头时始终校验完整性与绑定。
    caller_role: str | None = None
    if system_has_channel_bindings(db):
        if not identity.channel and not identity.external_user_id:
            raise HTTPException(
                status_code=403,
                detail="白名单已建立，请使用已绑定渠道身份创建成员",
            )
        caller_member = enforce_channel_member(
            db,
            body_member_id=None,
            channel=identity.channel,
            external_user_id=identity.external_user_id,
            authorization=identity.authorization,
        web_session_token=identity.web_session_token,

            ui_client=identity.ui_client,
        )
        caller = db.get(Member, caller_member) if caller_member else None
        caller_role = caller.role if caller else None
    elif identity.channel or identity.external_user_id:
        caller_member = enforce_channel_member(
            db,
            body_member_id=None,
            channel=identity.channel,
            external_user_id=identity.external_user_id,
            authorization=identity.authorization,
        web_session_token=identity.web_session_token,

            ui_client=identity.ui_client,
        )
        caller = db.get(Member, caller_member) if caller_member else None
        caller_role = caller.role if caller else None

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
        channel=identity.channel,
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