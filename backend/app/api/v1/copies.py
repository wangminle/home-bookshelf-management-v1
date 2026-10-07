from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope, resolve_body_member, verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.copy import CopyCreate, CopyOut
from app.schemas.storage import PlacementUpdate
from app.services import storage_placement
from app.services.copies import create_copy
from app.services.storage_tx import LocationError
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit
from app.utils.serializers import copy_to_dict

router = APIRouter(prefix="/books", tags=["copies"])


def _require_owner(request: Request, db: Session = Depends(get_db)):
    """Owner-only 依赖（延迟导入避免与 web_auth 循环引用，同 books.py/storage.py 范本）。"""
    from app.api.v1.web_auth import require_owner

    return require_owner(request, db)


@router.post("/{book_id}/copies", response_model=ApiResponse, status_code=201)
def add_copy(
    book_id: int,
    payload: CopyCreate,
    ctx: AuthContext = Depends(require_scope("books:write")),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = resolve_body_member(ctx, payload.owner_member_id, db=db)
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
        channel=ctx.channel,
        payload={"book_id": book_id, "copy_id": result.copy.id},
    )
    data = copy_to_dict(result.copy, include_placement=ctx.has_scope("locations:read"), db=db)
    data["message"] = result.message
    return ApiResponse(data=data)


@router.patch("/{book_id}/copies/{copy_id}/placement", response_model=ApiResponse)
def set_copy_placement(
    book_id: int,
    copy_id: int,
    payload: PlacementUpdate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """单册分配/移动/清除位置（LOC-13，§9）。仅 Owner Web。

    target 非 null=分配/移动；target=null=清除（须显式 keep_legacy_location）。
    """
    try:
        result = storage_placement.set_placement(
            db, book_id, copy_id, payload, operator_member_id=owner.id)
    except LocationError as exc:
        raise exc.as_http_exception() from exc
    return ApiResponse(data=result["copy"])
