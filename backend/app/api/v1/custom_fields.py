from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth import ChannelIdentity, channel_headers, enforce_channel_member
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.custom_field import CustomFieldCreate, CustomFieldOut
from app.services.custom_fields import upsert_custom_field
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


@router.post("", response_model=ApiResponse)
def upsert_field(
    payload: CustomFieldCreate,
    response: Response,
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = enforce_channel_member(
        db,
        body_member_id=None,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
        require_channel=True,
    )
    try:
        result = upsert_custom_field(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    response.status_code = 201 if result.created else 200
    log_and_commit(
        db,
        action="custom_field.upsert",
        member_id=member_id,
        channel=identity.channel,
        payload={
            "field_id": result.field.id,
            "entity_type": payload.entity_type,
            "entity_id": payload.entity_id,
            "field_key": payload.field_key,
        },
    )
    data = CustomFieldOut.model_validate(result.field).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
