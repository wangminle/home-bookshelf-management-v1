from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope
from app.db import get_db
from app.schemas.book import ApiResponse
from app.services.stats import get_stats

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=ApiResponse)
def stats(
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("stats:read")),
) -> ApiResponse:
    """BUG-167：统计（含 total_spent 等财务口径）接 stats:read 鉴权，匿名不再可读。"""
    return ApiResponse(data=get_stats(db).model_dump())
