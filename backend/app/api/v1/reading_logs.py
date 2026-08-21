from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope, resolve_body_member, verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.reading_log import ReadingLogCreate, ReadingLogOut
from app.services.reading_logs import create_reading_log
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit

router = APIRouter(prefix="/books", tags=["reading-logs"])


@router.post("/{book_id}/reading-logs", response_model=ApiResponse, status_code=201)
def add_reading_log(
    book_id: int,
    payload: ReadingLogCreate,
    ctx: AuthContext = Depends(require_scope("reading:write")),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = resolve_body_member(ctx, payload.member_id, db=db)
    payload = payload.model_copy(update={"member_id": member_id})
    try:
        result = create_reading_log(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(
        db,
        action="reading_log.create",
        operator_member_id=ctx.member_id,
        member_id=result.log.member_id,
        channel=ctx.channel,
        payload={"book_id": book_id, "log_id": result.log.id},
    )
    data = ReadingLogOut.model_validate(result.log).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)