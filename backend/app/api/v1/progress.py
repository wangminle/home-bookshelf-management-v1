from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope, resolve_body_member, verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.reading import ProgressOut, ProgressUpdate
from app.services.reading import update_reading_progress
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit

router = APIRouter(prefix="/books", tags=["reading"])


@router.post("/{book_id}/progress", response_model=ApiResponse)
def update_progress(
    book_id: int,
    payload: ProgressUpdate,
    response: Response,
    ctx: AuthContext = Depends(require_scope("reading:write")),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = resolve_body_member(ctx, payload.member_id, db=db)
    payload = payload.model_copy(update={"member_id": member_id})
    try:
        result = update_reading_progress(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    response.status_code = 201 if result.created else 200
    log_and_commit(
        db,
        action="progress.update",
        member_id=result.progress.member_id,
        channel=ctx.channel,
        payload={"book_id": book_id, "progress_id": result.progress.id},
    )

    data = ProgressOut(
        id=result.progress.id,
        book_id=result.progress.book_id,
        member_id=result.progress.member_id,
        status=result.progress.status,
        current_page=result.progress.current_page,
        percent=result.progress.percent,
        rating=result.progress.rating,
        to_read=result.progress.to_read,
        finish_date=result.progress.finish_date,
        updated_at=result.progress.updated_at,
        message=result.message,
    )
    return ApiResponse(data=data.model_dump())
