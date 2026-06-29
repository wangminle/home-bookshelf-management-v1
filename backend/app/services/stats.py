from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Book, Member, PurchaseRecord, ReadingLog, ReadingProgress
from app.schemas.stats import CategoryCount, MemberStats, StatsOut
from app.utils.time_helpers import local_today_iso


def _reading_streak(db: Session, member_id: int) -> int:
    from datetime import date, timedelta

    member = db.get(Member, member_id)
    offset = member.reading_streak_offset if member else 0

    dates = db.scalars(
        select(ReadingLog.log_date)
        .where(ReadingLog.member_id == member_id)
        .distinct()
        .order_by(ReadingLog.log_date.desc())
    ).all()
    if not dates:
        return offset

    date_set = set(dates)
    streak = 0
    current = date.fromisoformat(local_today_iso())
    while current.isoformat() in date_set:
        streak += 1
        current -= timedelta(days=1)

    return streak + offset


def get_stats(db: Session) -> StatsOut:
    total_books = db.scalar(select(func.count()).select_from(Book)) or 0

    status_rows = db.execute(
        select(ReadingProgress.status, func.count()).group_by(ReadingProgress.status)
    ).all()
    by_status = {row[0]: row[1] for row in status_rows}
    for key in ("unread", "reading", "finished", "abandoned", "dropped"):
        by_status.setdefault(key, 0)

    category_rows = db.execute(
        select(Book.category, func.count())
        .where(Book.category.is_not(None), Book.category != "")
        .group_by(Book.category)
        .order_by(func.count().desc())
    ).all()
    by_category = [CategoryCount(category=row[0] or "未分类", count=row[1]) for row in category_rows]

    total_spent = float(
        db.scalar(
            select(func.coalesce(func.sum(PurchaseRecord.price), 0)).where(
                func.coalesce(PurchaseRecord.currency, "CNY") == "CNY"
            )
        )
        or 0
    )
    purchase_count = db.scalar(select(func.count()).select_from(PurchaseRecord)) or 0
    pages_total = db.scalar(select(func.coalesce(func.sum(ReadingLog.pages_read), 0))) or 0

    members = db.scalars(select(Member).order_by(Member.id)).all()
    member_stats: list[MemberStats] = []
    for member in members:
        reading = db.scalar(
            select(func.count()).select_from(ReadingProgress).where(
                ReadingProgress.member_id == member.id,
                ReadingProgress.status == "reading",
            )
        ) or 0
        finished = db.scalar(
            select(func.count()).select_from(ReadingProgress).where(
                ReadingProgress.member_id == member.id,
                ReadingProgress.status == "finished",
            )
        ) or 0
        member_stats.append(
            MemberStats(
                id=member.id,
                name=member.name,
                books_reading=reading,
                books_finished=finished,
                reading_streak=_reading_streak(db, member.id),
            )
        )

    return StatsOut(
        total_books=total_books,
        by_status=by_status,
        by_category=by_category,
        total_spent=round(total_spent, 2),
        purchase_count=purchase_count,
        reading_logs_pages_total=int(pages_total),
        members=member_stats,
    )
