from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.reading import ProgressOut, ProgressUpdate
from app.services.reading import update_reading_progress
from app.utils.db_errors import ConflictError

router = APIRouter(prefix="/books", tags=["reading"])


@router.post("/{book_id}/progress", response_model=ApiResponse)
def update_progress(
    book_id: int,
    payload: ProgressUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        result = update_reading_progress(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    data = ProgressOut(
        id=result.progress.id,
        book_id=result.progress.book_id,
        member_id=result.progress.member_id,
        status=result.progress.status,
        current_page=result.progress.current_page,
        percent=result.progress.percent,
        rating=result.progress.rating,
        finish_date=result.progress.finish_date,
        updated_at=result.progress.updated_at,
        message=result.message,
    )
    return ApiResponse(data=data.model_dump())
