from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.copy import CopyCreate, CopyOut
from app.services.copies import create_copy
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_operation
from app.utils.serializers import copy_to_out

router = APIRouter(prefix="/books", tags=["copies"])


@router.post("/{book_id}/copies", response_model=ApiResponse, status_code=201)
def add_copy(book_id: int, payload: CopyCreate, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        result = create_copy(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_operation(db, action="copy.create", payload={"book_id": book_id, "copy_id": result.copy.id})
    db.commit()
    data = copy_to_out(result.copy).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
