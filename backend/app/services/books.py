from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Attachment, Book, BookCopy, BookTag, CustomField, PurchaseRecord, ReadingNote, ReadingProgress, Tag
from app.schemas.book import BookUpdate
from app.utils.book_helpers import canonical_isbn13, isbn_lookup_keys, normalize_isbn, normalize_title, serialize_json_list
from app.utils.db_errors import ConflictError, rollback_on_integrity
from app.utils.serializers import book_detail_to_dict


@dataclass
class BookUpdateResult:
    book: Book
    message: str


def sync_book_tags(db: Session, book_id: int, tag_names: list[str]) -> list[str]:
    cleaned = list(dict.fromkeys(name.strip() for name in tag_names if name and name.strip()))
    db.execute(delete(BookTag).where(BookTag.book_id == book_id))
    db.flush()

    result: list[str] = []
    for name in cleaned:
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        db.add(BookTag(book_id=book_id, tag_id=tag.id))
        result.append(tag.name)
    return result


def _normalize_updated_isbns(book: Book, payload: BookUpdate, set_fields: set[str] | None = None) -> tuple[str | None, str | None]:
    """根据 payload 更新 ISBN。set_fields 区分未传与显式 null（BUG-118）。"""
    if set_fields is None:
        set_fields = payload.model_fields_set
    next_isbn13 = book.isbn13
    next_isbn10 = book.isbn10

    if "isbn13" in set_fields:
        val = payload.isbn13
        next_isbn13 = (canonical_isbn13(val) or val.strip() or None) if val else None
    if "isbn10" in set_fields:
        val = payload.isbn10
        next_isbn10 = (normalize_isbn(val) or val.strip() or None) if val else None
        derived_isbn13 = canonical_isbn13(next_isbn10) if next_isbn10 else None
        if derived_isbn13:
            next_isbn13 = derived_isbn13

    if next_isbn10 and next_isbn13:
        derived_isbn13 = canonical_isbn13(next_isbn10)
        if derived_isbn13 and derived_isbn13 != next_isbn13:
            raise ConflictError("isbn10 与 isbn13 不一致")

    return next_isbn13, next_isbn10


def _ensure_no_conflicting_isbn(db: Session, *, book_id: int, isbn13: str | None, isbn10: str | None) -> None:
    lookup_keys = isbn_lookup_keys(isbn13) | isbn_lookup_keys(isbn10)
    if not lookup_keys:
        return

    duplicate = db.scalar(
        select(Book).where(
            Book.id != book_id,
            or_(
                Book.isbn13.in_(lookup_keys),
                Book.isbn10.in_(lookup_keys),
            ),
        )
    )
    if duplicate:
        raise ConflictError("书籍已存在（ISBN 冲突）")


def update_book(db: Session, book_id: int, payload: BookUpdate) -> BookUpdateResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    # BUG-118：使用 model_fields_set 区分"未传字段"与"显式传 null"
    # 之前 `if payload.X is not None:` 会把显式 null 当成"跳过"，无法清空字段
    set_fields = payload.model_fields_set

    if "title" in set_fields and payload.title is not None:
        book.title = payload.title.strip()
        book.normalized_title = normalize_title(payload.title)
    if "subtitle" in set_fields:
        book.subtitle = payload.subtitle
    if "isbn13" in set_fields or "isbn10" in set_fields:
        next_isbn13, next_isbn10 = _normalize_updated_isbns(book, payload, set_fields)
        _ensure_no_conflicting_isbn(db, book_id=book_id, isbn13=next_isbn13, isbn10=next_isbn10)
        book.isbn13 = next_isbn13
        book.isbn10 = next_isbn10
    if "authors" in set_fields:
        # BUG-118：显式传 null 应清空作者（serialize_json_list(None) -> None）
        book.authors = serialize_json_list(payload.authors)
    if "publisher" in set_fields:
        book.publisher = payload.publisher
    if "publish_date" in set_fields:
        book.publish_date = payload.publish_date
    if "page_count" in set_fields:
        book.page_count = payload.page_count
    if "language" in set_fields:
        book.language = payload.language
    if "category" in set_fields:
        book.category = payload.category
    if "summary" in set_fields:
        book.summary = payload.summary

    if "tags" in set_fields:
        # BUG-118：显式传 null 清空所有标签（sync_book_tags([]) 删除全部 BookTag）
        sync_book_tags(db, book_id, payload.tags or [])

    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(book)
    return BookUpdateResult(book=book, message=f"已更新《{book.title}》")


def get_book_detail(db: Session, book_id: int) -> dict:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    copies = db.scalars(select(BookCopy).where(BookCopy.book_id == book_id)).all()
    progress_list = db.scalars(select(ReadingProgress).where(ReadingProgress.book_id == book_id)).all()
    purchases = db.scalars(select(PurchaseRecord).where(PurchaseRecord.book_id == book_id)).all()
    notes = db.scalars(select(ReadingNote).where(ReadingNote.book_id == book_id)).all()
    attachments = db.scalars(
        select(Attachment).where(Attachment.entity_type == "book", Attachment.entity_id == book_id)
    ).all()
    custom_fields = db.scalars(
        select(CustomField).where(CustomField.entity_type == "book", CustomField.entity_id == book_id)
    ).all()
    tag_rows = db.scalars(
        select(Tag.name).join(BookTag, BookTag.tag_id == Tag.id).where(BookTag.book_id == book_id)
    ).all()

    return book_detail_to_dict(
        book,
        copies=copies,
        progress_list=progress_list,
        purchases=purchases,
        notes=notes,
        attachments=attachments,
        tags=list(tag_rows),
        custom_fields=custom_fields,
    )
