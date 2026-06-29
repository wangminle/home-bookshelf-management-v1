from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.custom_field import CustomFieldCreate, CustomFieldOut
from app.services.custom_fields import upsert_custom_field
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_operation

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


@router.post("", response_model=ApiResponse, status_code=201)
def upsert_field(payload: CustomFieldCreate, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        result = upsert_custom_field(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_operation(db, action="custom_field.upsert", payload={"field_id": result.field.id})
    db.commit()
    data = CustomFieldOut.model_validate(result.field).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
