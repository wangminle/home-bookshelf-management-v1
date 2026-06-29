from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, Member, PurchaseRecord
from app.services.metadata import fetch_metadata
from app.services.recognition import recognize_isbn_from_image
from app.services.storage import download_cover, save_uploaded_image
from app.utils.book_helpers import (
    author_in_json_list,
    canonical_isbn13,
    deserialize_json_list,
    isbn_lookup_keys,
    normalize_isbn,
    normalize_title,
    serialize_json_dict,
    serialize_json_list,
)
from app.utils.db_errors import rollback_on_integrity
from app.utils.time_helpers import utc_today_iso


@dataclass
class IntakeInput:
    isbn: str | None = None
    title: str | None = None
    author: str | None = None
    authors: list[str] | None = None
    image_path: Path | None = None
    price: float | None = None
    channel: str | None = None
    location: str | None = None
    member_id: int | None = None


@dataclass
class IntakeResult:
    action: str
    book: Book
    matched_source: str | None
    isbn_detected: str | None
    message: str
    created_copy: bool = False
    created_purchase: bool = False
    already_exists: bool = False


def intake_book(db: Session, payload: IntakeInput) -> IntakeResult:
    _validate_intake(payload)

    isbn_detected: str | None = normalize_isbn(payload.isbn)
    image_saved_path: str | None = None

    if payload.image_path and payload.image_path.exists():
        if not isbn_detected:
            isbn_detected = recognize_isbn_from_image(payload.image_path)
        image_saved_path = save_uploaded_image(
            payload.image_path,
            target_name=canonical_isbn13(isbn_detected) or isbn_detected or payload.image_path.stem,
        )

    authors = payload.authors or ([payload.author] if payload.author else None)
    metadata = fetch_metadata(isbn=isbn_detected, title=payload.title, author=payload.author)

    if metadata:
        title = metadata.title
        subtitle = metadata.subtitle
        isbn13, isbn10 = _resolve_isbn_fields(metadata.isbn13, metadata.isbn10, isbn_detected)
        authors = metadata.authors or authors
        publisher = metadata.publisher
        publish_date = metadata.publish_date
        page_count = metadata.page_count
        language = metadata.language
        category = metadata.category
        summary = metadata.summary
        source = metadata.source
        openlibrary_id = metadata.openlibrary_id
        google_books_id = metadata.google_books_id
        extra = serialize_json_dict(metadata.extra)
        cover_url = metadata.cover_url
    else:
        if not payload.title and not isbn_detected:
            raise ValueError("无法识别书籍信息，请提供 ISBN、书名或清晰的书封条码照片")
        title = payload.title or f"ISBN {isbn_detected}"
        subtitle = None
        isbn13, isbn10 = _resolve_isbn_fields(None, None, isbn_detected)
        publisher = publish_date = page_count = language = category = summary = None
        source = "manual"
        openlibrary_id = None
        google_books_id = None
        extra = None
        cover_url = None

    existing = _find_existing(db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors)
    if existing:
        return _handle_existing_book(db, existing, payload, metadata, isbn_detected, source)

    cover_path = image_saved_path
    if not cover_path and cover_url:
        cover_target = isbn13 or normalize_title(title)
        cover_path = download_cover(cover_url, target_name=cover_target)

    book = Book(
        title=title.strip(),
        subtitle=subtitle,
        isbn13=isbn13,
        isbn10=isbn10,
        normalized_title=normalize_title(title),
        authors=serialize_json_list(authors),
        publisher=publisher,
        publish_date=publish_date,
        page_count=page_count,
        language=language,
        category=category,
        summary=summary,
        cover_path=cover_path,
        openlibrary_id=openlibrary_id,
        google_books_id=google_books_id,
        extra=extra,
        source=source,
    )
    db.add(book)
    db.flush()

    created_purchase = False
    created_copy = False
    copy_id: int | None = None
    if payload.location:
        copy = BookCopy(
            book_id=book.id,
            copy_type="physical",
            location=payload.location,
            owner_member_id=payload.member_id,
            acquire_type="purchased" if payload.price is not None else None,
            status="in_shelf",
        )
        db.add(copy)
        db.flush()
        copy_id = copy.id
        created_copy = True

    if payload.price is not None:
        _create_purchase(db, book, payload, copy_id=copy_id)
        created_purchase = True

    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(book)

    message = f"已入库《{book.title}》"
    if created_copy:
        message += "，已登记副本"
    if created_purchase:
        message += "，已记录购买"

    return IntakeResult(
        action="created",
        book=book,
        matched_source=source if metadata else "manual",
        isbn_detected=isbn_detected,
        message=message,
        created_copy=created_copy,
        created_purchase=created_purchase,
    )


def _validate_intake(payload: IntakeInput) -> None:
    if payload.price is not None and payload.price <= 0:
        raise ValueError("价格必须大于 0")


def _resolve_isbn_fields(
    meta_isbn13: str | None,
    meta_isbn10: str | None,
    detected: str | None,
) -> tuple[str | None, str | None]:
    isbn13 = canonical_isbn13(meta_isbn13) or canonical_isbn13(meta_isbn10) or canonical_isbn13(detected)
    isbn10 = normalize_isbn(meta_isbn10)
    if not isbn10:
        normalized = normalize_isbn(detected)
        if normalized and len(normalized) == 10:
            isbn10 = normalized
    return isbn13, isbn10


def _handle_existing_book(
    db: Session,
    existing: Book,
    payload: IntakeInput,
    metadata,
    isbn_detected: str | None,
    source: str | None,
) -> IntakeResult:
    created_purchase = False
    created_copy = False

    if payload.location:
        copy = BookCopy(
            book_id=existing.id,
            copy_type="physical",
            location=payload.location,
            owner_member_id=payload.member_id,
            acquire_type="purchased" if payload.price is not None else None,
            status="in_shelf",
        )
        db.add(copy)
        db.flush()
        created_copy = True
        copy_id = copy.id
    else:
        copy_id = None

    if payload.price is not None:
        _create_purchase(db, existing, payload, copy_id=copy_id)
        created_purchase = True

    if created_copy or created_purchase:
        try:
            db.commit()
        except IntegrityError as exc:
            raise rollback_on_integrity(db, exc) from exc

    message = f"《{existing.title}》已在书架中"
    if created_copy:
        message += "，已添加新副本"
    if created_purchase:
        message += "，已记录购买"

    return IntakeResult(
        action="exists",
        book=existing,
        matched_source=source if metadata else None,
        isbn_detected=isbn_detected,
        message=message,
        created_copy=created_copy,
        created_purchase=created_purchase,
        already_exists=True,
    )


def _find_existing(
    db: Session,
    *,
    isbn13: str | None,
    isbn10: str | None,
    title: str,
    authors: list[str] | None,
) -> Book | None:
    lookup_keys: set[str] = set()
    if isbn13:
        lookup_keys |= isbn_lookup_keys(isbn13)
    if isbn10:
        lookup_keys |= isbn_lookup_keys(isbn10)

    if lookup_keys:
        found = db.scalar(
            select(Book).where(
                or_(Book.isbn13.in_(lookup_keys), Book.isbn10.in_(lookup_keys))
            )
        )
        if found:
            return found

    normalized = normalize_title(title)
    candidates = db.scalars(select(Book).where(Book.normalized_title == normalized)).all()
    if not candidates:
        return None
    if not authors:
        return candidates[0] if len(candidates) == 1 else None

    author_hint = authors[0].strip().lower()
    matched = [book for book in candidates if _authors_match(book.authors, author_hint)]
    return matched[0] if len(matched) == 1 else None


def _authors_match(book_authors_raw: str | None, author_hint: str) -> bool:
    book_authors = deserialize_json_list(book_authors_raw) or []
    if not book_authors:
        return False
    return any(name.strip().lower() == author_hint for name in book_authors)


def _create_purchase(db: Session, book: Book, payload: IntakeInput, copy_id: int | None = None) -> None:
    if payload.member_id is not None:
        member = db.get(Member, payload.member_id)
        if not member:
            raise ValueError(f"成员 ID {payload.member_id} 不存在")

    db.add(
        PurchaseRecord(
            book_id=book.id,
            copy_id=copy_id,
            price=payload.price,
            channel=payload.channel,
            buyer_member_id=payload.member_id,
            purchase_date=utc_today_iso(),
        )
    )
