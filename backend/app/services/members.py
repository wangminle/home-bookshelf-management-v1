from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Member
from app.schemas.member import MemberBind
from app.utils.db_errors import rollback_on_integrity


@dataclass
class MemberBindResult:
    member: Member
    message: str


def list_members(db: Session) -> list[Member]:
    return db.scalars(select(Member).order_by(Member.id)).all()


def bind_member_channel(db: Session, payload: MemberBind) -> MemberBindResult:
    member = db.get(Member, payload.member_id)
    if not member:
        raise ValueError(f"成员 ID {payload.member_id} 不存在")

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
