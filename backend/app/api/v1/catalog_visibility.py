"""权限阶段 4：目录可见性批量管理与 C→B 切换预览（Owner 专用）。

基线 §14 阶段 4 任务 2/3：
- Owner 可对单书或批量书目设置可见级别（单书走 PATCH /books/{id}/visibility）；
- 提供 C→B 切换预览：列出 explicit_public 模式下"将继续公开"与"将消失"的书；
- 模式本身由 ANONYMOUS_CATALOG_MODE 环境配置（切换需重启，不批量篡改数据；
  回滚 = 切回 lan_shared，私有记录任何模式不会意外公开）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth_context import verify_csrf
from app.db import get_db
from app.models import Book
from app.schemas.book import ApiResponse, BookVisibilityBatchUpdate
from app.services.catalog_read import effective_visibility
from app.utils.operation_log import log_and_commit

router = APIRouter(prefix="/catalog-visibility", tags=["catalog-visibility"])


def _require_owner(request: Request, db: Session = Depends(get_db)):
    from app.api.v1.web_auth import require_owner

    return require_owner(request, db)


# 切换预览的分页上限（预览是只读列表，防超大库一次拉全量）
_PREVIEW_LIMIT = 500


@router.post("/batch", response_model=ApiResponse)
def batch_set_visibility(
    payload: BookVisibilityBatchUpdate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """Owner 批量设置可见级别（单批 ≤500，防误操作面过大）。"""
    books = db.scalars(select(Book).where(Book.id.in_(payload.book_ids))).all()
    found_ids = {b.id for b in books}
    missing = [i for i in payload.book_ids if i not in found_ids]
    changed = 0
    for book in books:
        if (book.catalog_visibility or "lan_shared") != payload.visibility:
            book.catalog_visibility = payload.visibility
            changed += 1
    db.commit()
    log_and_commit(
        db,
        action="book.visibility.batch",
        member_id=owner.id,
        payload={
            "requested": len(payload.book_ids),
            "changed": changed,
            "missing": missing,
            "new_visibility": payload.visibility,
            "operator_member_id": owner.id,
        },
    )
    return ApiResponse(data={
        "requested": len(payload.book_ids), "changed": changed, "missing": missing,
        "catalog_visibility": payload.visibility,
    })


@router.get("/preview", response_model=ApiResponse)
def preview_mode_switch(
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
) -> ApiResponse:
    """C→B 切换预览（基线 §14-阶段4-3；CHK-086：SQL 聚合+LIMIT，不拉全量）。

    列出 explicit_public 模式下：将继续公开（public）与将从匿名书架消失
    （lan_shared/members_only/private，含兼容读取的未标记存量）的书，
    附计数。members_only/private 在两种模式下都不可匿名见，单独计数。
    """
    from sqlalchemy import case, func

    vis_expr = func.coalesce(Book.catalog_visibility, "lan_shared")
    base_count = select(func.count()).select_from(Book)
    total = db.scalar(base_count) or 0

    # 聚合计数（不加载行数据）
    remain_count = db.scalar(base_count.where(vis_expr == "public")) or 0
    never_count = db.scalar(
        base_count.where(vis_expr.in_(("members_only", "private")))
    ) or 0
    disappear_count = total - remain_count - never_count

    # SQL LIMIT 获取预览行（分别取前 N 条）
    remain_rows = db.execute(
        select(Book.id, Book.title, Book.catalog_visibility)
        .where(vis_expr == "public")
        .order_by(Book.id).limit(_PREVIEW_LIMIT)
    ).all()
    disappear_rows = db.execute(
        select(Book.id, Book.title, Book.catalog_visibility)
        .where(vis_expr.notin_(("public", "members_only", "private")))
        .order_by(Book.id).limit(_PREVIEW_LIMIT)
    ).all()

    def _to_items(rows):
        return [{"id": r.id, "title": r.title, "visibility": effective_visibility(r.catalog_visibility)} for r in rows]

    return ApiResponse(data={
        "current_mode": _current_mode(),
        "target_mode": "explicit_public",
        "summary": {
            "total": total,
            "remain_public": remain_count,
            "disappear_from_anonymous": disappear_count,
            "never_anonymous": never_count,
        },
        "remain_public": _to_items(remain_rows),
        "disappear": _to_items(disappear_rows),
        "truncated": remain_count > _PREVIEW_LIMIT or disappear_count > _PREVIEW_LIMIT,
    })


def _current_mode() -> str:
    from app.config import settings

    return settings.anonymous_catalog_mode
