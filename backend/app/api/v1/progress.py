from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth import ChannelIdentity, channel_headers, enforce_channel_member
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
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = enforce_channel_member(
        db,
        body_member_id=payload.member_id,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
    )
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
        channel=identity.channel,
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
