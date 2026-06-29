from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, ReadingLog
from app.schemas.reading_log import ReadingLogCreate
from app.utils.db_errors import rollback_on_integrity
from app.utils.member_helpers import resolve_member_id


@dataclass
class ReadingLogResult:
    log: ReadingLog
    book: Book
    message: str


def create_reading_log(db: Session, book_id: int, payload: ReadingLogCreate) -> ReadingLogResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    member_id = resolve_member_id(db, payload.member_id)
    log = ReadingLog(
        book_id=book_id,
        member_id=member_id,
        log_date=payload.log_date,
        pages_read=payload.pages_read,
        minutes_read=payload.minutes_read,
        notes=payload.notes,
        session_start=payload.session_start,
        session_end=payload.session_end,
    )
    db.add(log)
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(log)

    hint = f"读了 {payload.pages_read} 页" if payload.pages_read else "已记录阅读"
    return ReadingLogResult(log=log, book=book, message=f"《{book.title}》{payload.log_date} {hint}")
