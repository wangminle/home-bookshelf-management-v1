from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Book, Member, PurchaseRecord, ReadingLog, ReadingProgress
from app.schemas.stats import CategoryCount, MemberStats, StatsOut, YearlyStat
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
    today = date.fromisoformat(local_today_iso())
    # 今天还没记日志时从昨天起算，避免白天看报表显示连续 0 天
    if today.isoformat() in date_set:
        current = today
    elif (today - timedelta(days=1)).isoformat() in date_set:
        current = today - timedelta(days=1)
    else:
        return offset

    streak = 0
    while current.isoformat() in date_set:
        streak += 1
        current -= timedelta(days=1)

    return streak + offset


def _compute_yearly_stats(db: Session, member_id: int | None = None) -> list[YearlyStat]:
    """按年度聚合入库数、CNY 花费、阅读页数。purchase_date / log_date 均为 String(10) ISO 日期。"""
    # 年度入库数：按 Book.created_at 的年份分组
    books_by_year: dict[str, int] = {}
    book_year_rows = db.execute(
        select(
            func.strftime("%Y", Book.created_at).label("yr"),
            func.count(),
        ).group_by("yr")
    ).all()
    for row in book_year_rows:
        if row.yr:
            books_by_year[row.yr] = row[1]

    # 年度花费：按 purchase_date 前 4 位分组（仅 CNY，与 total_spent 同口径）
    spent_by_year: dict[str, float] = {}
    cny_filter = func.coalesce(PurchaseRecord.currency, "CNY") == "CNY"
    spend_where = [cny_filter, PurchaseRecord.purchase_date.is_not(None)]
    if member_id is not None:
        spend_where.append(PurchaseRecord.buyer_member_id == member_id)
    spend_rows = db.execute(
        select(
            func.substr(PurchaseRecord.purchase_date, 1, 4).label("yr"),
            func.coalesce(func.sum(PurchaseRecord.price), 0),
        )
        .where(*spend_where)
        .group_by("yr")
    ).all()
    for row in spend_rows:
        if row.yr:
            spent_by_year[row.yr] = round(float(row[1]), 2)

    # 年度阅读页数：按 log_date 前 4 位分组
    pages_by_year: dict[str, int] = {}
    pages_where = []
    if member_id is not None:
        pages_where.append(ReadingLog.member_id == member_id)
    page_rows = db.execute(
        select(
            func.substr(ReadingLog.log_date, 1, 4).label("yr"),
            func.coalesce(func.sum(ReadingLog.pages_read), 0),
        ).where(*pages_where).group_by("yr")
    ).all()
    for row in page_rows:
        if row.yr:
            pages_by_year[row.yr] = int(row[1])

    all_years = sorted(set(books_by_year) | set(spent_by_year) | set(pages_by_year), reverse=True)
    return [
        YearlyStat(
            year=yr,
            books_added=books_by_year.get(yr, 0),
            spent=spent_by_year.get(yr, 0),
            pages_read=pages_by_year.get(yr, 0),
        )
        for yr in all_years
    ]


def get_stats(db: Session, member_id: int | None = None) -> StatsOut:
    """统计聚合。

    BUG-191：member_id 提供时按该成员范围聚合（进度/购买/日志/成员列表/
    年度趋势均收敛到本人；书目总数与分类保留家庭共享口径）——Member 默认
    仅本人统计，全家庭口径仅 Web Owner 或持有 stats:household 的主体。
    """
    _member_progress = ReadingProgress.member_id == member_id if member_id is not None else True
    _member_purchase = PurchaseRecord.buyer_member_id == member_id if member_id is not None else True
    _member_logs = ReadingLog.member_id == member_id if member_id is not None else True

    total_books = db.scalar(select(func.count()).select_from(Book)) or 0

    # BUG-117/123：每本书聚合成单一全局状态，使 by_status 总和 <= total_books，
    # 且统计与 GET /books?status=X 列表筛选口径一致。
    # 优先级：finished > reading > abandoned/dropped > unread。
    progress_rows = db.execute(
        select(ReadingProgress.book_id, ReadingProgress.status).where(_member_progress)
    ).all()
    book_member_statuses: dict[int, list[str]] = {}
    for book_id, status in progress_rows:
        book_member_statuses.setdefault(book_id, []).append(status)

    by_status = {key: 0 for key in ("unread", "reading", "finished", "abandoned", "dropped")}
    for _book_id, statuses in book_member_statuses.items():
        from app.utils.book_helpers import aggregate_book_status

        by_status[aggregate_book_status(statuses)] += 1
    # 无任何进度记录的书一律计为 unread（成员口径下同样以家庭藏书数为分母）
    books_with_progress = len(book_member_statuses)
    by_status["unread"] += max(total_books - books_with_progress, 0)

    category_rows = db.execute(
        select(Book.category, func.count())
        .where(Book.category.is_not(None), Book.category != "")
        .group_by(Book.category)
        .order_by(func.count().desc())
    ).all()
    by_category = [CategoryCount(category=row[0] or "未分类", count=row[1]) for row in category_rows]

    cny_filter = func.coalesce(PurchaseRecord.currency, "CNY") == "CNY"
    total_spent = float(
        db.scalar(
            select(func.coalesce(func.sum(PurchaseRecord.price), 0))
            .where(cny_filter, _member_purchase)
        ) or 0
    )
    # 与 total_spent 同口径：仅统计 CNY（缺省视为 CNY）购买笔数
    purchase_count = db.scalar(
        select(func.count()).select_from(PurchaseRecord).where(cny_filter, _member_purchase)
    ) or 0
    pages_total = db.scalar(
        select(func.coalesce(func.sum(ReadingLog.pages_read), 0)).where(_member_logs)
    ) or 0

    if member_id is not None:
        members = db.scalars(
            select(Member).where(Member.id == member_id).order_by(Member.id)
        ).all()
    else:
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
        by_year=_compute_yearly_stats(db, member_id=member_id),
    )
