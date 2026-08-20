"""BUG-163 回归测试：无 ISBN 入库去重受外部元数据波动影响。

根因：intake.py 先用 OpenLibrary 结果改写书名，再执行去重。
相同输入可能因一次命中（书名被改写）、一次超时（书名保持原样）
而创建两本书。

修复：normalized_title 基于原始输入书名（稳定），去重时同时尝试
元数据书名和原始输入书名，确保两种情况都能命中已有记录。
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Book
from app.services.intake import IntakeInput, intake_book
from app.utils.book_helpers import normalize_title


def _make_meta(title: str, author: str):
    """构造一个简易元 dataclass 替代真实 fetch_metadata 返回。"""

    class _M:
        def __init__(self):
            self.title = title
            self.subtitle = None
            self.isbn13 = None
            self.isbn10 = None
            self.authors = [author]
            self.publisher = None
            self.publish_date = None
            self.page_count = None
            self.language = None
            self.category = None
            self.summary = None
            self.source = "openlibrary"
            self.openlibrary_id = "OL123"
            self.google_books_id = None
            self.extra = None
            self.cover_url = None

    return _M()


def test_metadata_hit_then_timeout_dedup_hits_original_title(db_engine):
    """第一次元数据命中（改写书名），第二次元数据超时（保持原样）→ 第二次应命中去重。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    call_count = [0]

    def fake_fetch(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            # 第一次：返回英文译名，改写书名
            return _make_meta("The Three-Body Problem", "Liu Cixin")
        # 第二次：超时/未命中，返回 None
        return None

    with patch("app.services.intake.fetch_metadata", side_effect=fake_fetch):
        with SessionLocal() as s1:
            r1 = intake_book(s1, IntakeInput(title="三体", author="刘慈欣"))
        assert r1.action == "created"
        # 显示书名被元数据改写为英文译名
        assert r1.book.title == "The Three-Body Problem"
        # 但 normalized_title 应基于原始输入书名
        assert r1.book.normalized_title == normalize_title("三体")

        with SessionLocal() as s2:
            r2 = intake_book(s2, IntakeInput(title="三体", author="刘慈欣"))
        # 第二次应命中去重，而非创建新书
        assert r2.action == "exists", (
            f"BUG-163：第二次入库应命中去重，实际 action={r2.action}"
        )

    # 最终只有一本书
    with SessionLocal() as s:
        count = s.scalar(
            select(func.count()).select_from(Book).where(
                Book.normalized_title == normalize_title("三体")
            )
        )
    assert count == 1, f"BUG-163：应只有 1 本书，实际 {count}"


def test_metadata_timeout_then_hit_dedup_hits_via_fallback(db_engine):
    """反过来：第一次超时（原样书名），第二次命中（改写书名）→ 第二次也应命中去重。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    call_count = [0]

    def fake_fetch(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return None  # 超时
        return _make_meta("The Three-Body Problem", "Liu Cixin")  # 命中

    with patch("app.services.intake.fetch_metadata", side_effect=fake_fetch):
        with SessionLocal() as s1:
            r1 = intake_book(s1, IntakeInput(title="三体", author="刘慈欣"))
        assert r1.action == "created"
        assert r1.book.title == "三体"  # 无元数据，保持原样

        with SessionLocal() as s2:
            r2 = intake_book(s2, IntakeInput(title="三体", author="刘慈欣"))
        # 第二次元数据命中改写书名，但去重应通过原始输入书名 fallback 命中
        assert r2.action == "exists", (
            f"BUG-163：第二次入库（元数据命中）应通过原始书名 fallback 命中去重，"
            f"实际 action={r2.action}"
        )

    with SessionLocal() as s:
        count = s.scalar(
            select(func.count()).select_from(Book).where(
                Book.normalized_title == normalize_title("三体")
            )
        )
    assert count == 1


def test_normalized_title_stable_with_metadata(db_engine):
    """有元数据命中时，normalized_title 仍基于原始输入书名。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    with patch(
        "app.services.intake.fetch_metadata",
        return_value=_make_meta("English Title", "Some Author"),
    ):
        with SessionLocal() as s:
            r = intake_book(s, IntakeInput(title="中文书名", author="作者"))
    assert r.action == "created"
    assert r.book.title == "English Title"  # 显示用元数据书名
    assert r.book.normalized_title == normalize_title("中文书名")  # 去重用原始书名


def test_no_title_isbn_only_uses_metadata_title_for_normalized(db_engine):
    """无书名仅有 ISBN 时，normalized_title 基于元数据/ISBN 回退书名（无原始书名）。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    meta = _make_meta("ISBN Book Title", "Author")
    meta.isbn13 = "9780306406157"

    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            r = intake_book(s, IntakeInput(isbn="9780306406157"))
    assert r.action == "created"
    # 无原始书名时回退到元数据书名
    assert r.book.normalized_title == normalize_title("ISBN Book Title")
