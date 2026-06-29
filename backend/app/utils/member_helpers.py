from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member


def resolve_member_id(db: Session, member_id: int | None) -> int:
    if member_id is not None:
        member = db.get(Member, member_id)
        if not member:
            raise ValueError(f"成员 ID {member_id} 不存在")
        return member_id

    member = db.scalar(select(Member).order_by(Member.id.asc()).limit(1))
    if member:
        return member.id

    member = Member(name="默认用户", role="owner")
    db.add(member)
    db.flush()
    return member.id
