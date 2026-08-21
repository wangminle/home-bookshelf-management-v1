"""Catalog Read Model 服务（权限阶段 1）。

Public Catalog 与后续 MCP 的唯一书目读取实现（MCP 设计 §4/§9.3）：
- 查询层只读取白名单需要的列（CHK-071：不再整行加载 Book ORM，
  降低未来序列化误用风险），绝不触碰进度/日志/笔记/购买/附件关系；
- 输出经 CatalogBookSummary/Detail 白名单模型，extra=forbid 锁死契约；
- 标签（public_tags）：Tag 模型没有公开/私有分级，家庭标签可能含内部
  信息——在提供显式公开分类（默认不公开）之前一律不下发（CHK-071）；
- 稳定排序（updated_at DESC, id DESC）+ 分页。
"""
from __future__ import annotations

import json

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from app.models import Book, BookCopy
from app.schemas.catalog import CatalogBookDetail, CatalogBookSummary, CatalogSearchPage
from app.utils.book_helpers import escape_like, like_pattern

# 副本状态 → 脱敏库存状态（基线 §6.1 MCP 枚举：in_shelf/borrowed/unknown）
_IN_SHELF_STATUSES = frozenset({"in_shelf"})
_BORROWED_STATUSES = frozenset({"borrowed", "lent_out", "lent"})

# 查询层只加载白名单输出 + 排序需要的列（cover_path 仅用于构造缩略图 URL，
# 永不出现在响应里）
_CATALOG_COLUMNS = (
    Book.id, Book.title, Book.subtitle, Book.authors, Book.translators,
    Book.publisher, Book.publish_date, Book.edition, Book.language,
    Book.page_count, Book.category, Book.summary, Book.cover_path,
)

# 响应字段白名单（基线 §9.3）——测试与契约对照用
PUBLIC_CATALOG_FIELDS = frozenset({
    "id", "title", "subtitle", "authors", "translators",
    "publisher", "publish_date", "edition", "language",
    "page_count", "category", "summary",
    "cover_thumbnail_url", "public_tags", "availability_status",
})

# 敏感键 denylist（基线 §9.3/WBS-MCP-0）：任何输出不得出现
CATALOG_DENYLIST = frozenset({
    "owner_member_id", "member_id", "member_name", "exact_location", "location",
    "file_path", "cover_path", "extra", "channel_bindings",
    "reading_progress", "reading_logs", "reading_notes", "progress", "logs", "notes",
    "purchase_records", "purchases", "purchase_price", "purchase_channel", "price",
    "raw_attachment_url", "attachments", "operation_log", "isbn13", "isbn10",
})


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x) for x in parsed if x is not None]


def _cover_thumbnail_url(row) -> str | None:
    """封面缩略图走 Public Catalog 专用端点（信任门控+限流），不暴露磁盘路径。"""
    if not row["cover_path"]:
        return None
    return f"/api/v1/public-catalog/covers/{row['id']}"


def _availability_from_statuses(statuses: list[str]) -> str:
    if any(s in _IN_SHELF_STATUSES for s in statuses):
        return "in_shelf"
    if any(s in _BORROWED_STATUSES for s in statuses):
        return "borrowed"
    return "unknown"


def _to_summary(row, availability: str) -> CatalogBookSummary:
    return CatalogBookSummary(
        id=row["id"],
        title=row["title"],
        subtitle=row["subtitle"],
        authors=_parse_json_list(row["authors"]),
        translators=_parse_json_list(row["translators"]),
        publisher=row["publisher"],
        publish_date=row["publish_date"],
        edition=row["edition"],
        language=row["language"],
        page_count=row["page_count"],
        category=row["category"],
        summary=row["summary"],
        cover_thumbnail_url=_cover_thumbnail_url(row),
        # CHK-071：Tag 无公开/私有分级，家庭标签可能含内部信息——
        # 提供显式公开分类（默认不公开）之前一律下发空列表
        public_tags=[],
        availability_status=availability,
    )


def _availability_by_book(db: Session, book_ids: list[int]) -> dict[int, str]:
    if not book_ids:
        return {}
    rows = db.execute(
        select(BookCopy.book_id, BookCopy.status).where(BookCopy.book_id.in_(book_ids))
    ).all()
    statuses: dict[int, list[str]] = {}
    for book_id, status in rows:
        statuses.setdefault(book_id, []).append(status)
    return {book_id: _availability_from_statuses(sts) for book_id, sts in statuses.items()} | {
        book_id: "unknown" for book_id in book_ids if book_id not in statuses
    }


def _availability_condition(availability: str):
    """库存状态的 SQL 过滤条件（与 _availability_from_statuses 同口径）。"""
    in_shelf = select(BookCopy.book_id).where(BookCopy.status.in_(_IN_SHELF_STATUSES))
    borrowed = select(BookCopy.book_id).where(BookCopy.status.in_(_BORROWED_STATUSES))
    if availability == "in_shelf":
        return Book.id.in_(in_shelf)
    if availability == "borrowed":
        return and_(Book.id.in_(borrowed), not_(Book.id.in_(in_shelf)))
    # unknown：无副本（或副本状态不属于上述两类）
    return not_(or_(Book.id.in_(in_shelf), Book.id.in_(borrowed)))


def search_catalog(
    db: Session,
    *,
    query: str | None = None,
    author: str | None = None,
    category: str | None = None,
    language: str | None = None,
    availability: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CatalogSearchPage:
    """搜索家庭共享书目（L1 白名单输出）。

    query 匹配书名/作者/ISBN（ISBN 只用于匹配，永不返回）；
    至少一个条件时才缩小结果，无条件则按稳定排序分页（浏览模式由调用方门控）。
    """
    conditions = []
    if query:
        pattern = like_pattern(query)
        conditions.append(
            or_(
                Book.title.ilike(pattern, escape="\\"),
                Book.normalized_title.ilike(pattern, escape="\\"),
                Book.authors.ilike(pattern, escape="\\"),
                Book.isbn13.ilike(pattern, escape="\\"),
                Book.isbn10.ilike(pattern, escape="\\"),
            )
        )
    if author:
        # 与 books.py 同口径：authors 以 json.dumps 落库，对检索词做 JSON 转义后 LIKE
        author_json = json.dumps(author.strip(), ensure_ascii=False)[1:-1]
        conditions.append(Book.authors.ilike(f"%{escape_like(author_json)}%", escape="\\"))
    if category:
        conditions.append(Book.category.ilike(like_pattern(category), escape="\\"))
    if language:
        conditions.append(Book.language.ilike(like_pattern(language), escape="\\"))
    if availability:
        conditions.append(_availability_condition(availability))

    stmt = select(*_CATALOG_COLUMNS)
    count_stmt = select(func.count()).select_from(Book)
    if conditions:
        combined = conditions[0] if len(conditions) == 1 else and_(*conditions)
        stmt = stmt.where(combined)
        count_stmt = count_stmt.where(combined)

    total = db.scalar(count_stmt) or 0
    page = max(1, page)
    page_size = max(1, page_size)
    rows = db.execute(
        stmt.order_by(Book.updated_at.desc(), Book.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    ).mappings().all()
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    book_ids = [r["id"] for r in rows]
    availability_map = _availability_by_book(db, book_ids)
    items = [
        _to_summary(r, availability_map.get(r["id"], "unknown"))
        for r in rows
    ]
    return CatalogSearchPage(
        items=items, total=total, page=page, page_size=page_size, has_more=has_more
    )


def get_catalog_book(db: Session, book_id: int) -> CatalogBookDetail | None:
    """读取单本书的脱敏详情；不存在返回 None（调用方统一 404，防枚举区分）。"""
    row = db.execute(
        select(*_CATALOG_COLUMNS).where(Book.id == book_id)
    ).mappings().first()
    if row is None:
        return None
    availability = _availability_by_book(db, [book_id]).get(book_id, "unknown")
    summary = _to_summary(row, availability)
    return CatalogBookDetail(**summary.model_dump())
