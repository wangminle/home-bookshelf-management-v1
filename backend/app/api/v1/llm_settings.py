"""Owner 后台：多模态模型接口配置。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth_context import verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.llm_settings import LlmSettingsUpdate
from app.services import llm_settings as llm_settings_service
from app.utils.operation_log import log_and_commit

router = APIRouter(prefix="/settings/llm", tags=["llm-settings"])


def _require_owner(request: Request, db: Session = Depends(get_db)):
    from app.api.v1.web_auth import require_owner

    return require_owner(request, db)


@router.get("", response_model=ApiResponse)
def get_llm_settings(
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
):
    row = llm_settings_service.load_row(db)
    data = llm_settings_service.to_public(row) if row is not None else llm_settings_service.empty_public()
    return ApiResponse(data=data.model_dump())


@router.put("", response_model=ApiResponse)
def put_llm_settings(
    payload: LlmSettingsUpdate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
):
    try:
        row, audit = llm_settings_service.apply_update(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_and_commit(
        db,
        action="llm_settings.update",
        member_id=owner.id,
        operator_member_id=owner.id,
        channel="web",
        payload=audit,
        result="ok",
    )
    db.refresh(row)
    return ApiResponse(data=llm_settings_service.to_public(row).model_dump())
