"""BI-03 回归：字段确认来源、元数据优先级、多作者、ISBN 归属冲突（契约 §5）。

验收锚点：
- 确认《翦商》不被 Revelation 覆盖（prefer_confirmed）；
- ISBN 命中冲突不误关联（阻断 + isbn_ownership_conflict）；
- default 策略保持既有行为（BUG-163/169/171 兼容）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.services.intake import (
    WARNING_ISBN_CONFLICT,
    WARNING_METADATA_FIELD_CONFLICT,
    IntakeInput,
    intake_book,
)
from app.utils.book_helpers import deserialize_json_list

ISBN_SANTI = "9787536692930"
ISBN_HUOZHE = "9787506365437"
ISBN_JIANSHANG = "9787553820217"


def _make_meta(title=None, author=None, isbn13=None, source="openlibrary", subtitle=None):
    class _M:
        pass

    m = _M()
    m.title = title
    m.subtitle = subtitle
    m.isbn13 = isbn13
    m.isbn10 = None
    m.authors = [author] if author else None
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


def _codes(result) -> list[str]:
    return [w.code for w in (result.warnings or [])]


def _warning(result, code: str):
    return next(w for w in result.warnings or [] if w.code == code)


# ── 确认值优先（prefer_confirmed）──

def test_confirmed_title_not_overwritten_by_foreign_metadata(db_engine):
    """《翦商》不被外部英文译名 Revelation 覆盖；冲突可见（BI-03 验收锚点）。"""
    meta = _make_meta(title="Revelation", author="Li Shuo", isbn13=ISBN_JIANSHANG, source="openlibrary")
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(
                title="翦商", author="李硕",
                field_policy="prefer_confirmed",
                confirmed_fields=["title", "authors"],
            ))

    assert result.action == "created"
    assert result.book.title == "翦商"
    assert result.book.normalized_title  # 去重锚点基于原始输入（BUG-163 语义不变）
    conflict = _warning(result, WARNING_METADATA_FIELD_CONFLICT)
    assert conflict.field == "title"
    assert conflict.detail["confirmed"] == "翦商"
    assert conflict.detail["metadata"] == "Revelation"
    assert conflict.detail["source"] == "openlibrary"


def test_default_policy_keeps_metadata_rewrite_for_old_clients(db_engine):
    """不传新字段（default）时元数据仍可改写展示字段——旧客户端零改动兼容。"""
    meta = _make_meta(title="Revelation", author="Li Shuo", isbn13=ISBN_JIANSHANG)
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(title="翦商", author="李硕"))

    assert result.action == "created"
    assert result.book.title == "Revelation"
    assert WARNING_METADATA_FIELD_CONFLICT not in _codes(result)


def test_confirmed_authors_array_preserved_end_to_end(db_engine):
    """多作者数组端到端保留；外部单作者仅作冲突证据。"""
    meta = _make_meta(title="三体", author="Liu Cixin", isbn13=ISBN_SANTI, source="google_books")
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(
                title="三体", authors=["刘慈欣", "姚海军"],
                field_policy="prefer_confirmed",
            ))

    assert deserialize_json_list(result.book.authors) == ["刘慈欣", "姚海军"]
    conflict = _warning(result, WARNING_METADATA_FIELD_CONFLICT)
    assert conflict.field == "authors"


def test_metadata_fills_only_missing_fields(db_engine):
    """确认字段缺省时元数据补空：仅 ISBN 输入仍取元数据书名。"""
    meta = _make_meta(title="三体", author="刘慈欣", isbn13=ISBN_SANTI)
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(
                isbn=ISBN_SANTI, field_policy="prefer_confirmed",
            ))
    assert result.book.title == "三体"
    assert WARNING_METADATA_FIELD_CONFLICT not in _codes(result)


def test_confirmed_isbn_wins_over_metadata_isbn_with_warning(db_engine):
    """手工/确认 ISBN 与元数据 ISBN 不一致：保留确认值并告警。"""
    meta = _make_meta(title="翦商", author="李硕", isbn13=ISBN_SANTI, source="nlc")
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(
                isbn=ISBN_JIANSHANG, title="翦商",
                field_policy="prefer_confirmed", confirmed_fields=["isbn", "title"],
            ))
    assert result.book.isbn13 == ISBN_JIANSHANG
    conflict = _warning(result, WARNING_METADATA_FIELD_CONFLICT)
    assert conflict.field == "isbn"
    assert conflict.detail["metadata"] == ISBN_SANTI


def test_explicit_empty_confirmed_list_means_nothing_confirmed(db_engine):
    """显式空 confirmed_fields + prefer_confirmed：无确认字段，元数据照常补全。"""
    meta = _make_meta(title="Revelation", author="Li Shuo", isbn13=ISBN_JIANSHANG)
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(
                title="翦商", author="李硕",
                field_policy="prefer_confirmed", confirmed_fields=[],
            ))
    assert result.book.title == "Revelation"
    assert WARNING_METADATA_FIELD_CONFLICT not in _codes(result)


def test_unknown_field_policy_rejected(db_engine):
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with SessionLocal() as s:
        with pytest.raises(ValueError, match="未知的字段策略"):
            intake_book(s, IntakeInput(title="三体", field_policy="aggressive"))


# ── ISBN 归属冲突 ──

def test_isbn_ownership_conflict_blocks_binding(db_engine):
    """ISBN 命中已有书但书名/作者明显冲突 → 阻断（400），不误关联（验收锚点）。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            intake_book(s, IntakeInput(isbn=ISBN_HUOZHE, title="活着", author="余华"))
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            with pytest.raises(ValueError, match="ISBN 归属冲突"):
                intake_book(s, IntakeInput(isbn=ISBN_HUOZHE, title="许三观卖血记", author="余华2"))


def test_isbn_same_title_rebinds_normally(db_engine):
    """同 ISBN 同书名（仅大小写/空白差异）→ 正常命中 exists，不阻断。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            intake_book(s, IntakeInput(isbn=ISBN_HUOZHE, title="活着", author="余华"))
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(isbn=ISBN_HUOZHE, title=" 活着 ", author="余华"))
    assert result.action == "exists"


def test_isbn_only_rescan_binds_without_judgement(db_engine):
    """仅 ISBN 复扫（无书名无作者）：无从判别归属，维持既有绑定行为。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            intake_book(s, IntakeInput(isbn=ISBN_HUOZHE))
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(isbn=ISBN_HUOZHE))
    assert result.action == "exists"


def test_isbn_rebind_via_normalized_title_anchor(db_engine):
    """BUG-169 语义 + ISBN：展示书名被元数据改写后，以原始书名 + ISBN 再入库仍绑定。"""
    meta = _make_meta(title="The Three-Body Problem", author="Liu Cixin", isbn13=ISBN_SANTI)
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            r1 = intake_book(s, IntakeInput(title="三体", author="刘慈欣"))
    assert r1.book.title == "The Three-Body Problem"
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(isbn=ISBN_SANTI, title="三体", author="刘慈欣"))
    assert result.action == "exists"


def test_isbn_rebind_via_author_overlap(db_engine):
    """书名不同（元数据改写）但作者吻合 → 视为同一书放行绑定。"""
    meta = _make_meta(title="Revelation", author="李硕", isbn13=ISBN_JIANSHANG)
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        with SessionLocal() as s:
            intake_book(s, IntakeInput(title="翦商", author="李硕"))
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with SessionLocal() as s:
            result = intake_book(s, IntakeInput(isbn=ISBN_JIANSHANG, title="翦商：殷周之变与华夏新生", author="李硕"))
    assert result.action == "exists"


# ── API 层：字段策略/多作者/冲突错误透出 ──

def test_api_json_policy_and_authors_array(client, monkeypatch):
    monkeypatch.setattr("app.services.intake.fetch_metadata", lambda **kw: None)
    resp = client.post("/api/v1/books/intake/json", json={
        "title": "三体", "authors": ["刘慈欣", "姚海军"],
        "field_policy": "prefer_confirmed", "confirmed_fields": ["title", "authors"],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["book"]["authors"] == ["刘慈欣", "姚海军"]


def test_api_rejects_unknown_confirmed_field(client):
    resp = client.post("/api/v1/books/intake/json", json={
        "title": "三体", "confirmed_fields": ["publisher"],
    })
    assert resp.status_code == 422
    assert "不可声明确认的字段" in resp.text


def test_api_rejects_unknown_field_policy(client):
    resp = client.post("/api/v1/books/intake/json", json={
        "title": "三体", "field_policy": "aggressive",
    })
    assert resp.status_code == 422


def test_api_isbn_conflict_returns_400_with_code(client, monkeypatch):
    monkeypatch.setattr("app.services.intake.fetch_metadata", lambda **kw: None)
    r1 = client.post("/api/v1/books/intake/json",
                     json={"isbn": ISBN_HUOZHE, "title": "活着", "author": "余华"})
    assert r1.status_code == 201
    r2 = client.post("/api/v1/books/intake/json",
                     json={"isbn": ISBN_HUOZHE, "title": "许三观卖血记", "author": "李硕"})
    assert r2.status_code == 400
    assert "ISBN 归属冲突" in r2.json()["detail"]
    assert WARNING_ISBN_CONFLICT  # 码常量存在（CLI 侧映射见 batch_manifest）


def test_api_multipart_accepts_policy_and_repeated_authors(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.intake.fetch_metadata", lambda **kw: None)
    resp = client.post(
        "/api/v1/books/intake",
        data={
            "title": "三体",
            "authors": ["刘慈欣", "姚海军"],  # httpx 多值 → 重复表单字段
            "field_policy": "prefer_confirmed",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["book"]["authors"] == ["刘慈欣", "姚海军"]
    assert data["book"]["title"] == "三体"
