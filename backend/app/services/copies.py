from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, Member
from app.schemas.copy import CopyCreate
from app.utils.db_errors import rollback_on_integrity


@dataclass
class CopyResult:
    copy: BookCopy
    book: Book
    message: str


def create_copy(db: Session, book_id: int, payload: CopyCreate) -> CopyResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    if payload.owner_member_id is not None:
        member = db.get(Member, payload.owner_member_id)
        if not member:
            raise ValueError(f"成员 ID {payload.owner_member_id} 不存在")

    copy = BookCopy(
        book_id=book_id,
        copy_type=payload.copy_type,
        format=payload.format,
        location=payload.location,
        file_path=payload.file_path,
        owner_member_id=payload.owner_member_id,
        acquire_type=payload.acquire_type,
        status=payload.status,
        condition=payload.condition,
    )
    db.add(copy)
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(copy)

    location_hint = f"，位置 {payload.location}" if payload.location else ""
    return CopyResult(copy=copy, book=book, message=f"已为《{book.title}》新增副本{location_hint}")
