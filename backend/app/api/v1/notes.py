from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.note import NoteCreate, NoteOut
from app.services.notes import create_note
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_operation
from app.utils.serializers import note_to_out

router = APIRouter(prefix="/books", tags=["notes"])


@router.post("/{book_id}/notes", response_model=ApiResponse, status_code=201)
def add_note(book_id: int, payload: NoteCreate, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        result = create_note(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_operation(db, action="note.create", payload={"book_id": book_id, "note_id": result.note.id})
    db.commit()
    data = note_to_out(result.note).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
