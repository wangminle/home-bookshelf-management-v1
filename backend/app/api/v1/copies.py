from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import ChannelIdentity, channel_headers, enforce_channel_member
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
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = enforce_channel_member(
        db,
        body_member_id=payload.owner_member_id,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
        authorization=identity.authorization,
        web_session_token=identity.web_session_token,
        required_scope="books:write",
        ui_client=identity.ui_client,
        require_channel=True,
    )
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
        channel=identity.channel,
        payload={"book_id": book_id, "copy_id": result.copy.id},
    )
    data = copy_to_out(result.copy).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
