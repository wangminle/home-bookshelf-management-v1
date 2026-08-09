from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import ChannelIdentity, channel_headers, enforce_channel_member
from app.db import get_db
from app.models import Book, ReadingProgress
from app.schemas.book import ApiResponse, BookCreate, BookListOut, BookUpdate
from app.services.books import get_book_detail, update_book
from app.utils.book_helpers import (
    canonical_isbn13,
    escape_like,
    isbn_lookup_keys,
    like_pattern,
    normalize_isbn,
    normalize_title,
    serialize_json_list,
)
from app.utils.db_errors import ConflictError, rollback_on_integrity
from app.utils.operation_log import log_and_commit
from app.utils.serializers import book_to_out

router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=ApiResponse)
def list_books(
    keyword: str | None = Query(default=None),
    author: str | None = Query(default=None),
    isbn: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None, description="阅读状态过滤"),
    member_id: int | None = Query(default=None, description="配合 status 过滤指定成员"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ApiResponse:
    stmt = select(Book)
    count_stmt = select(func.count()).select_from(Book)
    conditions = []

    if keyword:
        pattern = like_pattern(keyword)
        conditions.append(
            or_(
                Book.title.ilike(pattern, escape="\\"),
                Book.normalized_title.ilike(pattern, escape="\\"),
            )
        )
    if author:
        # authors 以 json.dumps 落库；对检索词做 JSON 字符串转义后再拼 LIKE，避免 " \ 漏配
        import json as _json

        author_json = _json.dumps(author.strip(), ensure_ascii=False)[1:-1]  # 去掉首尾引号
        author_pattern = f"%{escape_like(author_json)}%"
        conditions.append(Book.authors.ilike(author_pattern, escape="\\"))
    if isbn:
        keys = isbn_lookup_keys(normalize_isbn(isbn) or isbn.strip())
        if keys:
            conditions.append(or_(Book.isbn13.in_(keys), Book.isbn10.in_(keys)))
        else:
            pattern = like_pattern(isbn)
            conditions.append(
                or_(
                    Book.isbn13.ilike(pattern, escape="\\"),
                    Book.isbn10.ilike(pattern, escape="\\"),
                )
            )
    if category:
        pattern = like_pattern(category)
        conditions.append(Book.category.ilike(pattern, escape="\\"))

    if member_id is not None and not status:
        raise HTTPException(status_code=400, detail="member_id 必须配合 status 参数一起使用")
    if status:
        # BUG-117/123：状态筛选口径与 GET /stats 完全一致——每本书聚合成单一全局状态。
        # 无 member_id 时按全部成员聚合；带 member_id 时仅按该成员的进度聚合。
        from app.utils.book_helpers import aggregate_book_status

        prog_stmt = select(ReadingProgress.book_id, ReadingProgress.status)
        if member_id is not None:
            prog_stmt = prog_stmt.where(ReadingProgress.member_id == member_id)
        progress_rows = db.execute(prog_stmt).all()
        book_member_statuses: dict[int, list[str]] = {}
        for bid, st in progress_rows:
            book_member_statuses.setdefault(bid, []).append(st)
        # 全局状态 == status 的书
        matched_book_ids = {
            bid
            for bid, statuses in book_member_statuses.items()
            if aggregate_book_status(statuses) == status
        }
        # unread 还要包含完全无进度记录的书（与 stats 口径一致）
        if status == "unread":
            books_with_progress_ids = set(book_member_statuses.keys())
            all_book_ids = set(db.scalars(select(Book.id)).all())
            matched_book_ids |= all_book_ids - books_with_progress_ids
        conditions.append(Book.id.in_(matched_book_ids) if matched_book_ids else Book.id == -1)

    if conditions:
        combined = conditions[0] if len(conditions) == 1 else and_(*conditions)
        stmt = stmt.where(combined)
        count_stmt = count_stmt.where(combined)

    total = db.scalar(count_stmt) or 0
    books = db.scalars(stmt.order_by(Book.updated_at.desc()).offset(offset).limit(limit)).all()
    return ApiResponse(data=BookListOut(items=[book_to_out(b) for b in books], total=total))


@router.post("", response_model=ApiResponse, status_code=201)
def create_book(
    payload: BookCreate,
    db: Session = Depends(get_db),
    identity: ChannelIdentity = Depends(channel_headers),
) -> ApiResponse:
    # BUG-102：白名单建立后，写入操作必须经过渠道身份鉴权
    enforce_channel_member(
        db,
        body_member_id=None,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
    )
    isbn13 = canonical_isbn13(payload.isbn13) or canonical_isbn13(payload.isbn10)
    isbn10 = normalize_isbn(payload.isbn10)
    lookup_keys = isbn_lookup_keys(isbn13) | isbn_lookup_keys(isbn10)
    if lookup_keys:
        existing = db.scalar(select(Book).where(or_(Book.isbn13.in_(lookup_keys), Book.isbn10.in_(lookup_keys))))
        if existing:
            raise HTTPException(status_code=409, detail="书籍已存在（ISBN 冲突）")

    book = Book(
        title=payload.title.strip(),
        subtitle=payload.subtitle,
        isbn13=isbn13,
        isbn10=isbn10,
        normalized_title=normalize_title(payload.title),
        authors=serialize_json_list(payload.authors),
        publisher=payload.publisher,
        publish_date=payload.publish_date,
        page_count=payload.page_count,
        language=payload.language,
        category=payload.category,
        summary=payload.summary,
        source="manual",
    )
    db.add(book)
    try:
        db.commit()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(rollback_on_integrity(db, exc))) from exc
    db.refresh(book)
    log_and_commit(db, action="book.create", payload={"book_id": book.id, "isbn13": book.isbn13, "title": book.title})
    return ApiResponse(data=book_to_out(book))


@router.get("/{book_id}", response_model=ApiResponse)
def get_book(book_id: int, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        data = get_book_detail(db, book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(data=data)


@router.patch("/{book_id}", response_model=ApiResponse)
def patch_book(
    book_id: int,
    payload: BookUpdate,
    db: Session = Depends(get_db),
    identity: ChannelIdentity = Depends(channel_headers),
) -> ApiResponse:
    # BUG-102：白名单建立后，写入操作必须经过渠道身份鉴权
    enforce_channel_member(
        db,
        body_member_id=None,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
    )
    try:
        result = update_book(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(db, action="book.update", payload={"book_id": book_id})
    return ApiResponse(data={**book_to_out(result.book).model_dump(), "message": result.message})
