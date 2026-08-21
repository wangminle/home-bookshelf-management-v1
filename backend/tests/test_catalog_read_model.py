"""Catalog Read Model 测试（权限阶段 1）：字段白名单、脱敏库存、搜索与分页、隐私哨兵。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, BookTag, Member, PurchaseRecord, ReadingNote, ReadingProgress, Tag
from app.schemas.catalog import CatalogBookSummary
from app.services import catalog_read
from app.utils.book_helpers import serialize_json_dict, serialize_json_list


# 敏感哨兵值（基线 §15.2/WBS-MCP-0 Task 0.2 同思路）
SENTINEL_MEMBER_NAME = "SENTINEL_MEMBER_张三"
SENTINEL_NOTE = "SENTINEL_NOTE_私人笔记"
SENTINEL_PRICE_MARK = "SENTINEL_PRICE"
SENTINEL_LOCATION = "SENTINEL_LOCATION_书房三层左二"
SENTINEL_ISBN = "9787302999901"


@pytest.fixture()
def seeded(db_session: Session) -> dict:
    member = Member(name=SENTINEL_MEMBER_NAME, role="member")
    db_session.add(member)
    db_session.commit()

    tag_a = Tag(name="科幻")
    tag_b = Tag(name="经典")
    db_session.add_all([tag_a, tag_b])
    db_session.commit()

    book_on_shelf = Book(
        title="三体", authors=serialize_json_list(["刘慈欣"]), publisher="重庆出版社",
        category="科幻", language="zh", page_count=302, summary="SENTINEL_SUMMARY_OK 简介文本",
        isbn13=SENTINEL_ISBN, cover_path="covers/santi.jpg",
        extra=serialize_json_dict({"secret": SENTINEL_LOCATION}),
    )
    book_borrowed = Book(
        title="小王子", authors=serialize_json_list(["安托万·德·圣-埃克苏佩里"]),
        publisher="人民文学出版社", category="童话", language="zh",
    )
    book_no_copy = Book(title="无副本书", authors=serialize_json_list(["某人"]), category="其他")
    db_session.add_all([book_on_shelf, book_borrowed, book_no_copy])
    db_session.commit()

    db_session.add_all([
        BookCopy(book_id=book_on_shelf.id, status="in_shelf", location=SENTINEL_LOCATION, owner_member_id=member.id),
        BookCopy(book_id=book_borrowed.id, status="lent_out", owner_member_id=member.id),
    ])
    db_session.add_all([
        BookTag(book_id=book_on_shelf.id, tag_id=tag_a.id),
        BookTag(book_id=book_on_shelf.id, tag_id=tag_b.id),
    ])
    # 敏感子资源哨兵
    db_session.add(ReadingProgress(book_id=book_on_shelf.id, member_id=member.id, status="reading"))
    db_session.add(ReadingNote(book_id=book_on_shelf.id, member_id=member.id, content_md=SENTINEL_NOTE))
    db_session.add(PurchaseRecord(
        book_id=book_on_shelf.id, buyer_member_id=member.id,
        price=99.5, original_price=120.0, channel=SENTINEL_PRICE_MARK,
    ))
    db_session.commit()
    return {
        "member": member,
        "on_shelf": book_on_shelf,
        "borrowed": book_borrowed,
        "no_copy": book_no_copy,
    }


# ── 字段白名单 ──


def test_summary_fields_exactly_match_whitelist() -> None:
    assert set(CatalogBookSummary.model_fields) == set(catalog_read.PUBLIC_CATALOG_FIELDS)


def test_summary_rejects_extra_fields() -> None:
    with pytest.raises(Exception):
        CatalogBookSummary(id=1, title="x", owner_member_id=2)


def test_availability_derivation(seeded: dict, db_session: Session) -> None:
    page = catalog_read.search_catalog(db_session)
    by_title = {item.title: item.availability_status for item in page.items}
    assert by_title["三体"] == "in_shelf"
    assert by_title["小王子"] == "borrowed"  # lent_out 归一为 borrowed
    assert by_title["无副本书"] == "unknown"


def test_in_shelf_priority_over_borrowed(db_session: Session) -> None:
    book = Book(title="双副本书", authors=None)
    db_session.add(book)
    db_session.commit()
    db_session.add_all([
        BookCopy(book_id=book.id, status="borrowed"),
        BookCopy(book_id=book.id, status="in_shelf"),
    ])
    db_session.commit()
    detail = catalog_read.get_catalog_book(db_session, book.id)
    assert detail is not None and detail.availability_status == "in_shelf"


# ── 搜索与筛选 ──


def test_search_by_keyword_title(seeded: dict, db_session: Session) -> None:
    page = catalog_read.search_catalog(db_session, query="三体")
    assert [i.title for i in page.items] == ["三体"]


def test_search_by_keyword_matches_isbn_without_returning_it(seeded: dict, db_session: Session) -> None:
    page = catalog_read.search_catalog(db_session, query=SENTINEL_ISBN)
    assert [i.title for i in page.items] == ["三体"]
    assert "isbn13" not in page.items[0].model_dump()


def test_search_by_author(seeded: dict, db_session: Session) -> None:
    page = catalog_read.search_catalog(db_session, author="刘慈欣")
    assert [i.title for i in page.items] == ["三体"]


def test_filter_category_and_language(seeded: dict, db_session: Session) -> None:
    assert [i.title for i in catalog_read.search_catalog(db_session, category="科幻").items] == ["三体"]
    # 同秒创建时排序退化到 id DESC：小王子(id=2) 在三体(id=1) 前
    assert [i.title for i in catalog_read.search_catalog(db_session, language="zh").items] == ["小王子", "三体"]


def test_filter_availability(seeded: dict, db_session: Session) -> None:
    assert [i.title for i in catalog_read.search_catalog(db_session, availability="in_shelf").items] == ["三体"]
    assert [i.title for i in catalog_read.search_catalog(db_session, availability="borrowed").items] == ["小王子"]
    assert [i.title for i in catalog_read.search_catalog(db_session, availability="unknown").items] == ["无副本书"]


# ── 分页 ──


def test_pagination_total_and_has_more(seeded: dict, db_session: Session) -> None:
    page1 = catalog_read.search_catalog(db_session, page=1, page_size=2)
    assert page1.total == 3
    assert page1.has_more is True
    assert len(page1.items) == 2
    page2 = catalog_read.search_catalog(db_session, page=2, page_size=2)
    assert page2.has_more is False
    assert len(page2.items) == 1


# ── 详情 ──


def test_get_catalog_book(seeded: dict, db_session: Session) -> None:
    detail = catalog_read.get_catalog_book(db_session, seeded["on_shelf"].id)
    assert detail is not None
    assert detail.title == "三体"
    # CHK-071：Tag 无公开分级前不下发标签（种子数据里有标签也不返回）
    assert detail.public_tags == []
    assert detail.cover_thumbnail_url == f"/api/v1/public-catalog/covers/{seeded['on_shelf'].id}"


def test_get_catalog_book_missing_returns_none(seeded: dict, db_session: Session) -> None:
    assert catalog_read.get_catalog_book(db_session, 99999) is None


# ── 隐私哨兵：任何输出不包含敏感数据 ──


def _dump_all(db_session: Session) -> str:
    from sqlalchemy import select
    parts = [catalog_read.search_catalog(db_session).model_dump_json()]
    for book_id in db_session.scalars(select(Book.id)).all():
        detail = catalog_read.get_catalog_book(db_session, book_id)
        if detail is not None:
            parts.append(detail.model_dump_json())
    return "\n".join(parts)


def test_no_sentinel_leakage(seeded: dict, db_session: Session) -> None:
    dump = _dump_all(db_session)
    assert SENTINEL_MEMBER_NAME not in dump
    assert SENTINEL_NOTE not in dump
    assert SENTINEL_PRICE_MARK not in dump
    assert SENTINEL_LOCATION not in dump
    assert SENTINEL_ISBN not in dump  # ISBN 不在白名单
    assert "99.5" not in dump  # 金额


def test_no_denylist_keys_in_output(seeded: dict, db_session: Session) -> None:
    page = catalog_read.search_catalog(db_session)
    for item in page.items:
        dumped = item.model_dump()
        for key in catalog_read.CATALOG_DENYLIST:
            assert key not in dumped
