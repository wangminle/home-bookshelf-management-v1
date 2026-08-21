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
    ctx: AuthContext = Depends(require_scope("stats:read")),
) -> ApiResponse:
    """BUG-167：统计（含 total_spent 等财务口径）接 stats:read 鉴权，匿名不再可读。

    BUG-191：全家庭口径仅 Web Owner 或持有 stats:household 的主体可得；
    其余主体（member 渠道、普通 Agent）只返回本人范围的统计
    （进度/购买/日志/年度趋势收敛到本人，成员列表仅自己）。
    """
    if (ctx.auth_type == "web" and ctx.is_owner) or "stats:household" in ctx.scopes:
        data = get_stats(db)
    else:
        data = get_stats(db, member_id=ctx.member_id)
    return ApiResponse(data=data.model_dump())
