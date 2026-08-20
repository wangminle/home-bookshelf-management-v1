"""BUG-169 回归测试：以展示书名（元数据改写）再入库仍应命中去重。

根因：BUG-163 把 normalized_title 改为存原始输入书名后，行的去重索引键是
"三体" 这类原始输入；若用户/Agent 之后用书目详情里读到的展示书名
（如 "The Three-Body Problem"）再次入库，索引等值匹配不命中，插入重复书。
修复：_find_existing 在索引未命中时回退按展示书名（Book.title）逐行归一化比对。
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Book
from app.services.intake import IntakeInput, intake_book
from app.utils.book_helpers import normalize_title


def _make_meta(title: str, author: str):
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
            self.openlibrary_id = "OL169"
            self.google_books_id = None
            self.extra = None
            self.cover_url = None

    return _M()


def _count(SessionLocal, normalized: str) -> int:
    with SessionLocal() as s:
        return s.scalar(
            select(func.count()).select_from(Book).where(Book.normalized_title == normalized)
        )


def test_reintake_with_display_title_dedups(db_engine):
    """第一次以中文书名入库（书名被元数据改写为英文），之后以展示书名再入库 → 去重。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    with patch(
        "app.services.intake.fetch_metadata",
        return_value=_make_meta("The Three-Body Problem", "Liu Cixin"),
    ):
        with SessionLocal() as s:
            r1 = intake_book(s, IntakeInput(title="三体", author="刘慈欣"))
        assert r1.action == "created"
        assert r1.book.title == "The Three-Body Problem"
        assert r1.book.normalized_title == normalize_title("三体")

    # 第二次：用户从详情页读到展示书名，用它入库（无元数据）
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            r2 = intake_book(s, IntakeInput(title="The Three-Body Problem", author="刘慈欣"))
    assert r2.action == "exists", (
        f"BUG-169：以展示书名再入库应命中去重，实际 action={r2.action}"
    )
    assert _count(SessionLocal, normalize_title("三体")) == 1


def test_display_title_with_different_author_still_separate(db_engine):
    """展示书名相同但作者不同 → 不得误合并（BUG-171 语义在回退路径同样成立）。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    with patch(
        "app.services.intake.fetch_metadata",
        return_value=_make_meta("The Three-Body Problem", "Liu Cixin"),
    ):
        with SessionLocal() as s:
            r1 = intake_book(s, IntakeInput(title="三体", author="刘慈欣"))
        assert r1.action == "created"

    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            r2 = intake_book(s, IntakeInput(title="The Three-Body Problem", author="王小波"))
    assert r2.action == "created"
    assert _count(SessionLocal, normalize_title("三体")) == 1
    with SessionLocal() as s:
        total = s.scalar(select(func.count()).select_from(Book))
    assert total == 2
