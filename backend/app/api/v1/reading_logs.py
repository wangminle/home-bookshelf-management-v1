from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import ChannelIdentity, channel_headers, enforce_channel_member
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
        required_scope="reading:write",
        ui_client=identity.ui_client,
    )
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
        member_id=result.log.member_id,
        channel=identity.channel,
        payload={"book_id": book_id, "log_id": result.log.id},
    )
    data = ReadingLogOut.model_validate(result.log).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)