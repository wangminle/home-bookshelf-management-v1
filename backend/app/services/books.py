from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Attachment, Book, BookCopy, BookTag, CustomField, PurchaseRecord, ReadingNote, ReadingProgress, Tag
from app.schemas.book import BookUpdate
from app.utils.book_helpers import canonical_isbn13, normalize_isbn, normalize_title, serialize_json_list
from app.utils.db_errors import rollback_on_integrity
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


def update_book(db: Session, book_id: int, payload: BookUpdate) -> BookUpdateResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    if payload.title is not None:
        book.title = payload.title.strip()
        book.normalized_title = normalize_title(payload.title)
    if payload.subtitle is not None:
        book.subtitle = payload.subtitle
    if payload.isbn13 is not None:
        book.isbn13 = canonical_isbn13(payload.isbn13) or payload.isbn13.strip() or None
    if payload.isbn10 is not None:
        book.isbn10 = normalize_isbn(payload.isbn10) or payload.isbn10.strip() or None
    if payload.authors is not None:
        book.authors = serialize_json_list(payload.authors)
    if payload.publisher is not None:
        book.publisher = payload.publisher
    if payload.publish_date is not None:
        book.publish_date = payload.publish_date
    if payload.page_count is not None:
        book.page_count = payload.page_count
    if payload.language is not None:
        book.language = payload.language
    if payload.category is not None:
        book.category = payload.category
    if payload.summary is not None:
        book.summary = payload.summary

    if payload.tags is not None:
        sync_book_tags(db, book_id, payload.tags)

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
