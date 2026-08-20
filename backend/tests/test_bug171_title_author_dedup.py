"""BUG-171 回归测试：同书名不同作者的两本书不得被去重误合并。

根因：BUG-163 的 _find_existing_dedup 宽松回退（authors=None）在候选唯一时
无视作者直接命中——《Python编程》李四 会被挂到已入库的《Python编程》张三 那本上。
修复：新建时作者存原始输入值（与 normalized_title 同为稳定去重锚点），
去重只做严格作者匹配，去掉宽松回退；同时保住 BUG-163 的元数据波动场景。
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Book
from app.services.intake import IntakeInput, intake_book
from app.utils.book_helpers import deserialize_json_list, normalize_title


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
            self.openlibrary_id = "OL123"
            self.google_books_id = None
            self.extra = None
            self.cover_url = None

    return _M()


def _count_books(SessionLocal, normalized: str) -> int:
    with SessionLocal() as s:
        return s.scalar(
            select(func.count()).select_from(Book).where(Book.normalized_title == normalized)
        )


def test_same_title_different_authors_stay_two_books(db_engine):
    """《Python编程》张三 与 《Python编程》李四 应是两本书（无 ISBN、无元数据）。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s1:
            r1 = intake_book(s1, IntakeInput(title="Python编程", author="张三"))
        with SessionLocal() as s2:
            r2 = intake_book(s2, IntakeInput(title="Python编程", author="李四"))
    assert r1.action == "created" and r2.action == "created", (
        f"BUG-171：同书名不同作者被误合并（第二次 action={r2.action}）"
    )
    assert _count_books(SessionLocal, normalize_title("Python编程")) == 2


def test_same_title_different_authors_survive_metadata_rewrite(db_engine):
    """元数据把两本书改写成同一英文书名/英文作者，仍不得合并。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    metas = [_make_meta("Python Programming", "Zhang San"), _make_meta("Python Programming", "Li Si")]
    calls = [0]

    def fake_fetch(*a, **kw):
        meta = metas[min(calls[0], len(metas) - 1)]
        calls[0] += 1
        return meta

    with patch("app.services.intake.fetch_metadata", side_effect=fake_fetch):
        with SessionLocal() as s1:
            r1 = intake_book(s1, IntakeInput(title="Python编程", author="张三"))
        with SessionLocal() as s2:
            r2 = intake_book(s2, IntakeInput(title="Python编程", author="李四"))
    assert r1.action == "created" and r2.action == "created"
    assert _count_books(SessionLocal, normalize_title("Python编程")) == 2


def test_same_input_dedups_when_metadata_rewrites_authors_only(db_engine):
    """保 BUG-163：元数据只改作者、书名不变时，同一输入两次入库仍应去重。

    该场景依赖"原始输入书名+原始作者"的严格复查（新建存原始作者后可稳定命中），
    修复前由宽松回退兜底，修复后必须由严格路径覆盖。
    """
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch(
        "app.services.intake.fetch_metadata",
        return_value=_make_meta("三体", "Liu Cixin"),
    ):
        with SessionLocal() as s1:
            r1 = intake_book(s1, IntakeInput(title="三体", author="刘慈欣"))
        assert r1.action == "created"
        # 展示书名保留元数据值；作者按 BUG-171 修复存原始输入值（JSON 文本列）
        assert r1.book.title == "三体"
        assert deserialize_json_list(r1.book.authors) == ["刘慈欣"]

        with SessionLocal() as s2:
            r2 = intake_book(s2, IntakeInput(title="三体", author="刘慈欣"))
    assert r2.action == "exists", (
        f"BUG-163 场景回归：元数据改写作者后同一输入未去重（action={r2.action}）"
    )
    assert _count_books(SessionLocal, normalize_title("三体")) == 1
