from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope, resolve_body_member, verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.note import NoteCreate, NoteOut
from app.services.notes import create_note
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit
from app.utils.serializers import note_to_out

router = APIRouter(prefix="/books", tags=["notes"])


@router.post("/{book_id}/notes", response_model=ApiResponse, status_code=201)
def add_note(
    book_id: int,
    payload: NoteCreate,
    ctx: AuthContext = Depends(require_scope("notes:write")),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = resolve_body_member(ctx, payload.member_id, db=db)
    payload = payload.model_copy(update={"member_id": member_id})
    try:
        result = create_note(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(
        db,
        action="note.create",
        operator_member_id=ctx.member_id,
        member_id=result.note.member_id,
        channel=ctx.channel,
        payload={"book_id": book_id, "note_id": result.note.id},
    )
    data = note_to_out(result.note).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)