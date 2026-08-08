"""PLN-002 WP1: stats 年度聚合——入库/花费/阅读页数按年汇总，CNY 口径一致。"""

from datetime import datetime, timezone

from app.models import Book, BookCopy, Member, PurchaseRecord, ReadingLog
from app.services.stats import get_stats


def test_empty_db_by_year_is_empty(db_session):
    stats = get_stats(db_session)
    assert stats.by_year == []


def test_yearly_aggregation(db_session):
    """造 2024/2025 两年的数据，断言 by_year 按年降序聚合正确。"""
    m = Member(name="测试", role="owner")
    db_session.add(m)
    db_session.flush()

    # 2024: 2 本书 + 1 笔 CNY 购买 + 50 页日志
    b1 = Book(title="书A", created_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
    b2 = Book(title="书B", created_at=datetime(2024, 6, 1, tzinfo=timezone.utc))
    # 2025: 1 本书 + 1 笔 CNY 购买 + 100 页日志
    b3 = Book(title="书C", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    db_session.add_all([b1, b2, b3])
    db_session.flush()

    db_session.add_all([
        PurchaseRecord(book_id=b1.id, price=30.0, currency="CNY", purchase_date="2024-03-15"),
        PurchaseRecord(book_id=b3.id, price=50.0, currency="CNY", purchase_date="2025-01-20"),
    ])
    db_session.add_all([
        ReadingLog(book_id=b1.id, member_id=m.id, log_date="2024-05-10", pages_read=50),
        ReadingLog(book_id=b3.id, member_id=m.id, log_date="2025-02-01", pages_read=100),
    ])
    db_session.commit()

    stats = get_stats(db_session)
    years = {y.year: y for y in stats.by_year}

    assert "2024" in years and "2025" in years
    # 2024
    assert years["2024"].books_added == 2
    assert years["2024"].spent == 30.0
    assert years["2024"].pages_read == 50
    # 2025
    assert years["2025"].books_added == 1
    assert years["2025"].spent == 50.0
    assert years["2025"].pages_read == 100
    # 降序
    assert stats.by_year[0].year > stats.by_year[1].year


def test_non_cny_excluded_from_spent(db_session):
    """USD 购买不计入 by_year.spent（与 total_spent 同口径）。"""
    b = Book(title="外币书", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    db_session.add(b)
    db_session.flush()
    db_session.add(PurchaseRecord(book_id=b.id, price=99.0, currency="USD", purchase_date="2025-03-01"))
    db_session.commit()

    stats = get_stats(db_session)
    yr = next((y for y in stats.by_year if y.year == "2025"), None)
    assert yr is not None
    assert yr.spent == 0  # USD 不计入
    assert yr.books_added == 1


def test_null_purchase_date_grouped_separately_or_excluded(db_session):
    """purchase_date 为 NULL 的购买不计入 by_year（查询过滤了 is_not(None)）。"""
    b = Book(title="无日期购买", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    db_session.add(b)
    db_session.flush()
    db_session.add(PurchaseRecord(book_id=b.id, price=10.0, currency="CNY", purchase_date=None))
    db_session.commit()

    stats = get_stats(db_session)
    yr = next((y for y in stats.by_year if y.year == "2025"), None)
    assert yr is not None
    assert yr.spent == 0  # NULL 日期不计入
    assert yr.books_added == 1
