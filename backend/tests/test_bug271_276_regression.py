"""BUG-271 / BUG-276 回归。

BUG-271（P1）：ISBN 归属冲突的证据只能来自调用方显式提供的原始书名/作者；
元数据改写值与合成占位值（如纯 ISBN 输入的 "ISBN {isbn}"）不得充当证据，
否则纯 ISBN 复扫会被误判 "ISBN 归属冲突" 400，阻断正常 exists 绑定。

BUG-276（P2）：prefer_confirmed 下显式确认的空值（subtitle=''、authors=[]）
是有意清空，与"未提供"不同，不得被外部元数据回填；非空确认值仍优先（无回归）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.services.intake import (
    WARNING_METADATA_FIELD_CONFLICT,
    IntakeInput,
    intake_book,
)
from app.utils.book_helpers import deserialize_json_list

ISBN_HUOZHE = "9787506365437"
ISBN_SANTI = "9787536692930"
ISBN_JIANSHANG = "9787553820217"


def _make_meta(title=None, author=None, isbn13=None, source="openlibrary", subtitle=None, authors=None):
    class _M:
        pass

    m = _M()
    m.title = title
    m.subtitle = subtitle
    m.isbn13 = isbn13
    m.isbn10 = None
    m.authors = authors if authors is not None else ([author] if author else None)
    m.publisher = None
    m.publish_date = None
    m.page_count = None
    m.language = None
    m.category = None
    m.summary = None
    m.source = source
    m.openlibrary_id = "OL123"
    m.google_books_id = None
    m.extra = None
    m.cover_url = None
    return m


def _run(db_engine, payload: IntakeInput, metadata=None):
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=metadata):
        with SessionLocal() as s:
            return intake_book(s, payload)


# ── BUG-271：纯 ISBN 复扫不被合成/元数据派生证据误伤 ──

def test_isbn_only_rescan_metadata_down_binds_existing(db_engine):
    """书架已有《活着》（该 ISBN）；元数据服务故障；复扫同 ISBN → 绑定 exists，不 400。"""
    _run(db_engine, IntakeInput(isbn=ISBN_HUOZHE, title="活着", author="余华"), metadata=None)
    result = _run(db_engine, IntakeInput(isbn=ISBN_HUOZHE), metadata=None)
    assert result.action == "exists"
    assert result.book.title == "活着"


def test_isbn_only_rescan_foreign_metadata_title_binds_existing(db_engine):
    """纯 ISBN 复扫返回外文元数据（The Three-Body Problem / Liu Cixin），
    书架是《三体》/刘慈欣（同 ISBN）→ 视为同一书绑定 exists，不阻断。"""
    meta_cn = _make_meta(title="三体", author="刘慈欣", isbn13=ISBN_SANTI)
    _run(db_engine, IntakeInput(isbn=ISBN_SANTI, title="三体", author="刘慈欣"), metadata=meta_cn)
    meta_en = _make_meta(title="The Three-Body Problem", author="Liu Cixin",
                         isbn13=ISBN_SANTI, source="google_books")
    result = _run(db_engine, IntakeInput(isbn=ISBN_SANTI), metadata=meta_en)
    assert result.action == "exists"
    assert result.book.title == "三体"


def test_placeholder_then_real_metadata_rescan_binds_existing(db_engine):
    """首次入库元数据未命中 → 建占位书名 "ISBN {isbn}"；复扫同 ISBN 元数据恢复 →
    不因占位名与真实元数据书名的差异报归属冲突，绑定 exists。"""
    r1 = _run(db_engine, IntakeInput(isbn=ISBN_SANTI), metadata=None)
    assert r1.action == "created"
    assert r1.book.title == f"ISBN {ISBN_SANTI}"
    meta = _make_meta(title="三体", author="刘慈欣", isbn13=ISBN_SANTI)
    result = _run(db_engine, IntakeInput(isbn=ISBN_SANTI), metadata=meta)
    assert result.action == "exists"


def test_explicit_conflicting_user_title_still_blocked(db_engine):
    """真实保护不回归：用户显式输入他人书名 + 已有 ISBN → 仍 400（转人工核对）。"""
    _run(db_engine, IntakeInput(isbn=ISBN_HUOZHE, title="活着", author="余华"), metadata=None)
    with pytest.raises(ValueError, match="ISBN 归属冲突"):
        _run(db_engine, IntakeInput(isbn=ISBN_HUOZHE, title="许三观卖血记", author="李硕"),
             metadata=None)


def test_explicit_conflicting_user_title_blocked_with_metadata_present(db_engine):
    """有外部元数据时，用户显式书名的冲突判定仍以原始输入为证据（不误放行、不误杀）。"""
    _run(db_engine, IntakeInput(isbn=ISBN_HUOZHE, title="活着", author="余华"), metadata=None)
    meta = _make_meta(title="活着", author="余华", isbn13=ISBN_HUOZHE)
    with pytest.raises(ValueError, match="ISBN 归属冲突"):
        _run(db_engine, IntakeInput(isbn=ISBN_HUOZHE, title="许三观卖血记", author="李硕"),
             metadata=meta)


# ── BUG-276：显式确认的空值 = 有意清空，不得被元数据回填 ──

def test_confirmed_empty_subtitle_stays_cleared(db_engine):
    """确认 subtitle='' + prefer_confirmed：外部元数据副题不得回填。"""
    meta = _make_meta(title="翦商", author="李硕", isbn13=ISBN_JIANSHANG,
                      subtitle="殷周之变与华夏新生")
    result = _run(db_engine, IntakeInput(
        isbn=ISBN_JIANSHANG, title="翦商", author="李硕", subtitle="",
        field_policy="prefer_confirmed", confirmed_fields=["subtitle"],
    ), metadata=meta)
    assert result.action == "created"
    assert result.book.subtitle is None


def test_confirmed_empty_authors_stays_cleared(db_engine):
    """确认 authors=[] + prefer_confirmed：外部元数据作者不得回填。"""
    meta = _make_meta(title="三体", author="刘慈欣", isbn13=ISBN_SANTI)
    result = _run(db_engine, IntakeInput(
        isbn=ISBN_SANTI, title="三体", authors=[],
        field_policy="prefer_confirmed", confirmed_fields=["authors"],
    ), metadata=meta)
    assert result.action == "created"
    assert deserialize_json_list(result.book.authors) is None


def test_confirmed_nonempty_values_still_override_metadata(db_engine):
    """无回归：确认非空 subtitle/authors 仍覆盖元数据，冲突可见。"""
    meta = _make_meta(title="翦商", author="李硕", isbn13=ISBN_JIANSHANG,
                      subtitle="元数据副题", authors=["Meta Author"])
    result = _run(db_engine, IntakeInput(
        isbn=ISBN_JIANSHANG, title="翦商", author="李硕",
        subtitle="确认副题", authors=["确认作者"],
        field_policy="prefer_confirmed", confirmed_fields=["subtitle", "authors"],
    ), metadata=meta)
    assert result.book.subtitle == "确认副题"
    assert deserialize_json_list(result.book.authors) == ["确认作者"]
    fields = {w.field for w in result.warnings or [] if w.code == WARNING_METADATA_FIELD_CONFLICT}
    assert {"subtitle", "authors"} <= fields


def test_unconfirmed_subtitle_filled_by_metadata_default(db_engine):
    """对照：default 策略（未确认）下元数据照常补副题，行为不变。"""
    meta = _make_meta(title="翦商", author="李硕", isbn13=ISBN_JIANSHANG,
                      subtitle="殷周之变与华夏新生")
    result = _run(db_engine, IntakeInput(
        isbn=ISBN_JIANSHANG, title="翦商", author="李硕", subtitle="",
    ), metadata=meta)
    assert result.book.subtitle == "殷周之变与华夏新生"


# ── BUG-276 残留：省略 confirmed_fields 的自动推断同样保留显式清空 ──

def test_auto_inferred_confirmed_empty_subtitle_stays_cleared(db_engine):
    """省略 confirmed_fields：subtitle='' 是显式提供（清空），自动推断视为确认，
    外部元数据副题不得回填。"""
    meta = _make_meta(title="翦商", author="李硕", isbn13=ISBN_JIANSHANG,
                      subtitle="殷周之变与华夏新生")
    result = _run(db_engine, IntakeInput(
        isbn=ISBN_JIANSHANG, title="翦商", author="李硕", subtitle="",
        field_policy="prefer_confirmed",
    ), metadata=meta)
    assert result.action == "created"
    assert result.book.subtitle is None


def test_auto_inferred_confirmed_empty_authors_stays_cleared(db_engine):
    """省略 confirmed_fields：authors=[] 是显式清空，自动推断视为确认，
    外部元数据作者不得回填。"""
    meta = _make_meta(title="三体", author="刘慈欣", isbn13=ISBN_SANTI)
    result = _run(db_engine, IntakeInput(
        isbn=ISBN_SANTI, title="三体", authors=[],
        field_policy="prefer_confirmed",
    ), metadata=meta)
    assert result.action == "created"
    assert deserialize_json_list(result.book.authors) is None


def test_auto_inferred_missing_subtitle_still_filled_by_metadata(db_engine):
    """对照：subtitle 未提供（None）时自动推断不含 subtitle，元数据照常补全——
    修复不得过度阻断正常补空。"""
    meta = _make_meta(title="翦商", author="李硕", isbn13=ISBN_JIANSHANG,
                      subtitle="殷周之变与华夏新生")
    result = _run(db_engine, IntakeInput(
        isbn=ISBN_JIANSHANG, title="翦商", author="李硕",
        field_policy="prefer_confirmed",
    ), metadata=meta)
    assert result.book.subtitle == "殷周之变与华夏新生"


def test_intake_request_authors_empty_list_distinct_from_missing():
    """schema 清洗不得把 [] 洗成 None——显式清空与未提供必须可区分。"""
    from app.schemas.intake import IntakeRequest

    req = IntakeRequest(isbn=ISBN_SANTI, title="三体", authors=[],
                        field_policy="prefer_confirmed")
    assert req.authors == []
    assert IntakeRequest(isbn=ISBN_SANTI, title="三体").authors is None
