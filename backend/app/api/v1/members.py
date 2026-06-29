from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.member import MemberBind, MemberOut
from app.services.members import bind_member_channel, list_members
from app.utils.db_errors import ConflictError
from app.utils.serializers import member_bindings

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=ApiResponse)
def get_members(db: Session = Depends(get_db)) -> ApiResponse:
    members = list_members(db)
    items = [
        MemberOut(
            id=m.id,
            name=m.name,
            role=m.role,
            avatar_path=m.avatar_path,
            channel_bindings=member_bindings(m.channel_bindings),
            reading_streak_offset=m.reading_streak_offset,
            created_at=m.created_at,
            updated_at=m.updated_at,
        ).model_dump()
        for m in members
    ]
    return ApiResponse(data={"items": items, "total": len(items)})


@router.post("/bind", response_model=ApiResponse)
def bind_channel(payload: MemberBind, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        result = bind_member_channel(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    member = result.member
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
