from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import ChannelIdentity, channel_headers, enforce_channel_member
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
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = enforce_channel_member(
        db,
        body_member_id=payload.member_id,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
        authorization=identity.authorization,
        web_session_token=identity.web_session_token,
        required_scope="notes:write",
        ui_client=identity.ui_client,
    )
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
        member_id=result.note.member_id,
        channel=identity.channel,
        payload={"book_id": book_id, "note_id": result.note.id},
    )
    data = note_to_out(result.note).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)