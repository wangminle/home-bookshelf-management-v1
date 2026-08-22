from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import find_members_by_binding
from app.models import Member
from app.schemas.member import MemberBind, MemberCreate
from app.utils.db_errors import ConflictError, rollback_on_integrity


@dataclass
class MemberBindResult:
    member: Member
    message: str


@dataclass
class MemberCreateResult:
    member: Member
    message: str
    created: bool


def list_members(db: Session) -> list[Member]:
    return db.scalars(select(Member).order_by(Member.id)).all()


def member_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Member)) or 0


def create_member(db: Session, payload: MemberCreate) -> MemberCreateResult:
    from sqlalchemy import func

    from app.services.agent_access import ensure_unique_username

    # BUG-204：显式指定用户名时按大小写不敏感预检（CI 唯一索引口径，友好 409）
    if payload.username:
        exists = db.scalar(
            select(func.lower(Member.username)).where(
                func.lower(Member.username) == payload.username.strip().lower()
            )
        )
        if exists:
            raise ConflictError(f"用户名已被使用：{payload.username.strip()}")
    member = Member(
        name=payload.name,
        role=payload.role,
        avatar_path=payload.avatar_path,
        reading_streak_offset=payload.reading_streak_offset,
        username=payload.username or ensure_unique_username(db, payload.name),
    )
    db.add(member)
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(member)
    return MemberCreateResult(member=member, message=f"已创建成员 {member.name}", created=True)


def ensure_default_member(db: Session) -> Member:
    """空库初始化：当无任何成员时创建默认 owner。用于 doctor/bind 引导流程。"""
    existing = db.scalar(select(Member).order_by(Member.id.asc()).limit(1))
    if existing:
        return existing
    member = Member(name="默认用户", role="owner")
    db.add(member)
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(member)
    return member


def bind_member_channel(db: Session, payload: MemberBind) -> MemberBindResult:
    member = db.get(Member, payload.member_id)
    if not member:
        # 空库引导：当请求 member_id=1 且库中无任何成员时自动创建默认 owner
        if payload.member_id == 1 and member_count(db) == 0:
            member = Member(name="默认用户", role="owner")
            db.add(member)
            db.flush()
        else:
            raise ValueError(f"成员 ID {payload.member_id} 不存在")

    # 全局唯一：同一渠道外部身份只能绑定一个成员（允许同一成员改绑/幂等）
    holders = find_members_by_binding(db, payload.channel, payload.external_user_id)
    for holder in holders:
        if holder.id != member.id:
            raise ConflictError(
                f"渠道 {payload.channel} 用户 {payload.external_user_id} 已绑定成员 {holder.id}（{holder.name}）"
            )

    bindings: dict[str, str] = {}
    if member.channel_bindings:
        try:
            parsed = json.loads(member.channel_bindings)
            if isinstance(parsed, dict):
                bindings = {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            bindings = {}

    bindings[payload.channel] = payload.external_user_id
    member.channel_bindings = json.dumps(bindings, ensure_ascii=False)

    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(member)
    return MemberBindResult(
        member=member,
        message=f"成员 {member.name} 已绑定 {payload.channel}",
    )