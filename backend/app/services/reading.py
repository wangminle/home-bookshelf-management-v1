from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, ReadingProgress
from app.schemas.reading import ProgressUpdate
from app.utils.db_errors import rollback_on_integrity
from app.utils.member_helpers import resolve_member_id
from app.utils.time_helpers import local_today_iso

TERMINAL_STATUSES = frozenset({"finished", "abandoned", "dropped"})


@dataclass
class ProgressResult:
    progress: ReadingProgress
    book: Book
    message: str
    created: bool


def update_reading_progress(db: Session, book_id: int, payload: ProgressUpdate) -> ProgressResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    member_id = resolve_member_id(db, payload.member_id)
    progress = db.scalar(
        select(ReadingProgress).where(
            ReadingProgress.book_id == book_id,
            ReadingProgress.member_id == member_id,
        )
    )
    created = progress is None
    if created:
        progress = ReadingProgress(book_id=book_id, member_id=member_id)
        db.add(progress)

    previous_status = progress.status

    if payload.status:
        progress.status = payload.status
    elif payload.current_page is not None or payload.percent is not None:
        progress.status = "reading"

    if payload.current_page is not None:
        page = payload.current_page
        if book.page_count and book.page_count > 0:
            page = min(page, book.page_count)
        progress.current_page = page
        if book.page_count and book.page_count > 0:
            progress.percent = round(min(page / book.page_count * 100, 100), 1)
    elif payload.percent is not None:
        progress.percent = min(payload.percent, 100.0)

    if payload.rating is not None:
        progress.rating = payload.rating

    # finish_date 为用户可见的本地日历日，与 reading_logs.log_date / stats streak 同源
    if progress.status in TERMINAL_STATUSES and not progress.finish_date:
        progress.finish_date = local_today_iso()
    elif progress.status not in TERMINAL_STATUSES and previous_status in TERMINAL_STATUSES:
        progress.finish_date = None

    progress.last_read_at = datetime.now(timezone.utc)

    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(progress)

    if progress.status == "finished":
        message = f"《{book.title}》已标记为读完"
    elif progress.status == "abandoned":
        message = f"《{book.title}》已标记为弃读"
    elif progress.status == "dropped":
        message = f"《{book.title}》已标记为放弃"
    elif progress.current_page is not None:
        message = f"《{book.title}》阅读进度已更新至第 {progress.current_page} 页"
    elif progress.status == "reading":
        message = f"《{book.title}》已标记为在读"
    else:
        message = f"《{book.title}》阅读进度已更新"

    return ProgressResult(progress=progress, book=book, message=message, created=created)
