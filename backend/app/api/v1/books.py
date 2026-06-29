from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
from app.utils.operation_log import log_operation
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
        author_clean = escape_like(author.strip())
        conditions.append(
            or_(
                Book.authors.ilike(f'%"{author_clean}"%', escape="\\"),
                Book.authors.ilike(f'%"{author_clean.lower()}"%', escape="\\"),
            )
        )
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

    if status:
        progress_stmt = select(ReadingProgress.book_id).where(ReadingProgress.status == status)
        if member_id is not None:
            progress_stmt = progress_stmt.where(ReadingProgress.member_id == member_id)
        book_ids = db.scalars(progress_stmt).all()
        conditions.append(Book.id.in_(book_ids) if book_ids else Book.id == -1)

    if conditions:
        combined = conditions[0] if len(conditions) == 1 else and_(*conditions)
        stmt = stmt.where(combined)
        count_stmt = count_stmt.where(combined)

    total = db.scalar(count_stmt) or 0
    books = db.scalars(stmt.order_by(Book.updated_at.desc()).offset(offset).limit(limit)).all()
    return ApiResponse(data=BookListOut(items=[book_to_out(b) for b in books], total=total))


@router.post("", response_model=ApiResponse, status_code=201)
def create_book(payload: BookCreate, db: Session = Depends(get_db)) -> ApiResponse:
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
    log_operation(db, action="book.create", payload={"book_id": book.id})
    db.commit()
    return ApiResponse(data=book_to_out(book))


@router.get("/{book_id}", response_model=ApiResponse)
def get_book(book_id: int, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        data = get_book_detail(db, book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(data=data)


@router.patch("/{book_id}", response_model=ApiResponse)
def patch_book(book_id: int, payload: BookUpdate, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        result = update_book(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_operation(db, action="book.update", payload={"book_id": book_id})
    db.commit()
    return ApiResponse(data={**book_to_out(result.book).model_dump(), "message": result.message})
