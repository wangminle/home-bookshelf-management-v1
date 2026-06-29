from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.reading_log import ReadingLogCreate, ReadingLogOut
from app.services.reading_logs import create_reading_log
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_operation

router = APIRouter(prefix="/books", tags=["reading-logs"])


@router.post("/{book_id}/reading-logs", response_model=ApiResponse, status_code=201)
def add_reading_log(book_id: int, payload: ReadingLogCreate, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        result = create_reading_log(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_operation(
        db,
        action="reading_log.create",
        member_id=result.log.member_id,
        payload={"book_id": book_id, "log_id": result.log.id},
    )
    db.commit()
    data = ReadingLogOut.model_validate(result.log).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
