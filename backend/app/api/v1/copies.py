from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope, resolve_body_member, verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.copy import CopyCreate, CopyOut
from app.services.copies import create_copy
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit
from app.utils.serializers import copy_to_out

router = APIRouter(prefix="/books", tags=["copies"])


@router.post("/{book_id}/copies", response_model=ApiResponse, status_code=201)
def add_copy(
    book_id: int,
    payload: CopyCreate,
    ctx: AuthContext = Depends(require_scope("books:write")),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = resolve_body_member(ctx, payload.owner_member_id, db=db)
    payload = payload.model_copy(update={"owner_member_id": member_id})
    try:
        result = create_copy(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(
        db,
        action="copy.create",
        member_id=member_id,
        channel=ctx.channel,
        payload={"book_id": book_id, "copy_id": result.copy.id},
    )
    data = copy_to_out(result.copy).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
