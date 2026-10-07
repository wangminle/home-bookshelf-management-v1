"""LOC-08：位置查询、格子副本清单、三类未定位清单、分页与数量统计回归。

口径钉死（设计 §5.1/§6.3、M0 契约）：
- 路径实时生成「房间 · 书架 · 层 · 格」；仅到书架追加「格子待登记」；
  归档节点保留路径并标注「（已归档）」；无结构化定位回退旧 location 文字；
- 统计：已分配（全部定位实体副本）/ 在位（in_shelf+storage）/ 在架（in_shelf）/
  涉及书目数（distinct book_id）；电子副本不进实体占用统计；
- 三类未定位互不混算；清单 join/聚合查询，语句数不随行数增长。
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import (
    Book,
    BookCopy,
    Member,
    ShelfCell,
    ShelfLayer,
    StorageRoom,
    StorageShelf,
)
from tests.test_bug166_167_168_auth import _agent_token
from tests.test_storage_auth import _member_web_client
from tests.test_storage_crud import _create_room, _create_shelf


def _book(db: Session, title: str) -> Book:
    book = Book(title=title)
    db.add(book)
    db.flush()
    return book


def _copy(db: Session, book_id: int, *, copy_type="physical", status="in_shelf",
          location=None, shelf_id=None, cell_id=None, owner_id=None,
          file_path=None) -> BookCopy:
    copy = BookCopy(
        book_id=book_id, copy_type=copy_type, status=status, location=location,
        placement_shelf_id=shelf_id, placement_cell_id=cell_id,
        owner_member_id=owner_id, file_path=file_path,
    )
    db.add(copy)
    db.flush()
    return copy


def _detail_copy(client: TestClient, book_id: int, copy_id: int) -> dict:
    r = client.get(f"/api/v1/books/{book_id}")
    assert r.status_code == 200, r.text
    return next(c for c in r.json()["data"]["copies"] if c["id"] == copy_id)


# ── 位置路径实时生成 ──

def test_location_display_full_path_and_shelf_only(client: TestClient, db_session: Session) -> None:
    room = _create_room(client, key="q-room", code="RQ", name="书房")
    shelf = _create_shelf(client, room["id"], key="q-shelf", code="A01", layers=[
        {"label": "第 1 层", "cells": [{"label": "左格"}]},
    ])
    cell = shelf["layers"][0]["cells"][0]

    book = _book(db_session, "路径书")
    in_cell = _copy(db_session, book.id, shelf_id=shelf["id"], cell_id=cell["id"])
    shelf_only = _copy(db_session, book.id, shelf_id=shelf["id"])
    legacy = _copy(db_session, book.id, location="卧室床头")
    db_session.commit()

    c = _detail_copy(client, book.id, in_cell.id)
    assert c["location_display"] == "书房 · A01 书架 · 第 1 层 · 左格"
    c = _detail_copy(client, book.id, shelf_only.id)
    assert c["location_display"] == "书房 · A01 书架 · 格子待登记"
    # 无结构化定位 → 回退旧 location 文字（LOC-02）
    c = _detail_copy(client, book.id, legacy.id)
    assert c["placement"] is None
    assert c["location_display"] == "卧室床头"


def test_location_display_marks_archived_nodes(client: TestClient, db_session: Session) -> None:
    """归档节点保留路径并标注（历史数据：归档后不再允许新引用，这里直接落库模拟）。"""
    room = _create_room(client, key="q-room-a", code="RA", name="旧书房")
    shelf = _create_shelf(client, room["id"], key="q-shelf-a", code="B01", layers=[
        {"label": "第 1 层", "cells": [{"label": "右格"}]},
    ])
    cell = shelf["layers"][0]["cells"][0]
    r = client.patch(f"/api/v1/storage/rooms/{room['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 200, r.text
    book = _book(db_session, "归档路径书")
    copy = _copy(db_session, book.id, shelf_id=shelf["id"], cell_id=cell["id"])
    db_session.commit()

    c = _detail_copy(client, book.id, copy.id)
    assert c["location_display"] == "旧书房（已归档） · B01 书架 · 第 1 层 · 右格"


# ── 格子副本清单 ──

def test_cell_copies_list(client: TestClient, db_session: Session) -> None:
    room = _create_room(client, key="c-room", code="RC")
    shelf = _create_shelf(client, room["id"], key="c-shelf", code="C01", layers=[
        {"label": "第 1 层", "cells": [{"label": "左格"}]},
    ])
    cell = shelf["layers"][0]["cells"][0]
    owner_id = client.get("/auth/session").json()["member_id"]

    book = _book(db_session, "同书多册")
    c1 = _copy(db_session, book.id, shelf_id=shelf["id"], cell_id=cell["id"], owner_id=owner_id)
    c2 = _copy(db_session, book.id, shelf_id=shelf["id"], cell_id=cell["id"],
               status="lent_out")  # 同书多册同格、非在位仍列出
    other = _copy(db_session, _book(db_session, "别格书").id, shelf_id=shelf["id"])
    db_session.commit()

    r = client.get(f"/api/v1/storage/cells/{cell['id']}/copies")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 2  # 只含本格副本
    assert data["location_display"].endswith("左格")
    by_id = {i["copy_id"]: i for i in data["items"]}
    assert by_id[c1.id]["book_title"] == "同书多册"
    assert by_id[c1.id]["owner_member_id"] == owner_id
    assert by_id[c1.id]["owner_member_name"]  # join 到归属成员名
    assert by_id[c2.id]["status"] == "lent_out"

    # 分页
    r = client.get(f"/api/v1/storage/cells/{cell['id']}/copies?limit=1&offset=1")
    page = r.json()["data"]
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["copy_id"] == c2.id

    r = client.get("/api/v1/storage/cells/999999/copies")
    assert r.status_code == 404


# ── 三类未定位（互不混算） ──

def test_unlocated_three_categories(client: TestClient, db_session: Session) -> None:
    room = _create_room(client, key="u-room", code="RU")
    shelf = _create_shelf(client, room["id"], key="u-shelf", code="U01")
    cell = shelf["layers"][0]["cells"][0]

    b1 = _book(db_session, "未登记位置")  # 实体副本无位置无旧文字 → 类 1
    _copy(db_session, b1.id)
    b2 = _book(db_session, "仅旧文字")  # 实体副本有旧文字 → 类 2
    _copy(db_session, b2.id, location="老书架第三层")
    b3 = _book(db_session, "仅电子书")  # 只有电子副本 → 类 3
    _copy(db_session, b3.id, copy_type="digital", file_path="ebooks/x.epub")
    b4 = _book(db_session, "已定位书")  # 已定位 → 三类都不进
    _copy(db_session, b4.id, shelf_id=shelf["id"], cell_id=cell["id"])
    db_session.commit()

    r = client.get("/api/v1/storage/unlocated")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    cat1, cat2, cat3 = (data["copies_without_placement"],
                        data["legacy_location_only"],
                        data["books_without_physical_copy"])

    assert cat1["total"] == 1 and cat1["items"][0]["book_title"] == "未登记位置"
    assert cat2["total"] == 1
    assert cat2["items"][0]["location"] == "老书架第三层"
    assert cat3["total"] == 1
    assert cat3["items"][0]["book_title"] == "仅电子书"
    assert cat3["items"][0]["digital_copies"] == 1
    # 互不混算：已定位书与电子书副本不出现在任何一类
    all_text = str(data)
    assert "已定位书" not in all_text


def test_unlocated_pagination(client: TestClient, db_session: Session) -> None:
    for i in range(3):
        b = _book(db_session, f"未定位{i}")
        _copy(db_session, b.id)
    db_session.commit()
    r = client.get("/api/v1/storage/unlocated?limit=2&offset=0")
    cat1 = r.json()["data"]["copies_without_placement"]
    assert cat1["total"] == 3 and len(cat1["items"]) == 2
    r = client.get("/api/v1/storage/unlocated?limit=2&offset=2")
    assert len(r.json()["data"]["copies_without_placement"]["items"]) == 1


# ── 数量统计（§6.3） ──

def test_shelf_and_room_stats(client: TestClient, db_session: Session) -> None:
    room = _create_room(client, key="s-room", code="RS")
    shelf = _create_shelf(client, room["id"], key="s-shelf", code="S01")
    cell = shelf["layers"][0]["cells"][0]

    multi = _book(db_session, "多册书")
    _copy(db_session, multi.id, shelf_id=shelf["id"], cell_id=cell["id"])          # 在架
    _copy(db_session, multi.id, shelf_id=shelf["id"], cell_id=cell["id"])          # 在架（同书第二册）
    _copy(db_session, _book(db_session, "储藏书").id, shelf_id=shelf["id"], status="storage")
    _copy(db_session, _book(db_session, "外借书").id, shelf_id=shelf["id"], status="lent_out")
    _copy(db_session, _book(db_session, "遗失书").id, shelf_id=shelf["id"], status="lost")
    _copy(db_session, multi.id, copy_type="digital")  # 电子副本不进实体统计
    db_session.commit()

    r = client.get(f"/api/v1/storage/shelves/{shelf['id']}")
    stats = r.json()["data"]["stats"]
    assert stats == {
        "assigned_copies": 5,   # in_shelf×2 + storage + lent_out + lost
        "present_copies": 3,    # in_shelf×2 + storage
        "on_shelf_copies": 2,
        "books_involved": 4,    # 多册书只计一次
    }

    r = client.get("/api/v1/storage/rooms")
    room_item = next(i for i in r.json()["data"]["items"] if i["id"] == room["id"])
    assert room_item["stats"]["assigned_copies"] == 5
    assert room_item["stats"]["on_shelf_copies"] == 2


# ── 权限矩阵 ──

def test_query_permission_matrix(client: TestClient, anon_client: TestClient,
                                 db_session: Session) -> None:
    room = _create_room(client, key="p-room", code="RPQ")
    shelf = _create_shelf(client, room["id"], key="p-shelf", code="P01")
    cell_id = shelf["layers"][0]["cells"][0]["id"]

    assert anon_client.get("/api/v1/storage/unlocated").status_code == 401
    assert anon_client.get(f"/api/v1/storage/cells/{cell_id}/copies").status_code == 401

    with _member_web_client(db_session) as member_client:
        assert member_client.get("/api/v1/storage/unlocated").status_code == 200
        assert member_client.get(
            f"/api/v1/storage/cells/{cell_id}/copies").status_code == 200

    _, token = _agent_token(client, ["locations:read"])
    r = client.get("/api/v1/storage/unlocated",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    _, no_scope = _agent_token(client, ["books:read"])
    r = client.get("/api/v1/storage/unlocated",
                   headers={"Authorization": f"Bearer {no_scope}"})
    assert r.status_code == 403


# ── 无逐行查询扩张（N+1） ──

def _count_statements(db_engine, fn) -> int:
    count = 0

    def listener(*args):
        nonlocal count
        count += 1

    event.listen(db_engine, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(db_engine, "before_cursor_execute", listener)
    return count


def test_room_list_statement_count_independent_of_rows(client: TestClient, db_engine,
                                                       db_session: Session) -> None:
    # 基线含 1 个房间（0 房间时统计聚合早退，语句数天然更少，不构成对照）
    room = _create_room(client, key="n-room-base", code="NB")
    shelf = _create_shelf(client, room["id"], key="n-shelf-base", code="NB1")
    cell = shelf["layers"][0]["cells"][0]
    b = _book(db_session, "基线书")
    _copy(db_session, b.id, shelf_id=shelf["id"], cell_id=cell["id"])
    db_session.commit()
    baseline = _count_statements(db_engine, lambda: client.get("/api/v1/storage/rooms"))

    for i in range(5):
        room = _create_room(client, key=f"n-room-{i}", code=f"N{i}")
        shelf = _create_shelf(client, room["id"], key=f"n-shelf-{i}", code=f"NS{i}")
        cell = shelf["layers"][0]["cells"][0]
        b = _book(db_session, f"压测书{i}")
        _copy(db_session, b.id, shelf_id=shelf["id"], cell_id=cell["id"])
    db_session.commit()

    grown = _count_statements(db_engine, lambda: client.get("/api/v1/storage/rooms"))
    assert grown == baseline, f"语句数随行数增长: {baseline} → {grown}"


def test_cell_copies_statement_count_independent_of_rows(client: TestClient, db_engine,
                                                         db_session: Session) -> None:
    room = _create_room(client, key="n2-room", code="NQ")
    shelf = _create_shelf(client, room["id"], key="n2-shelf", code="NQ1")
    cell = shelf["layers"][0]["cells"][0]
    b = _book(db_session, "格内书")
    _copy(db_session, b.id, shelf_id=shelf["id"], cell_id=cell["id"])
    db_session.commit()

    baseline = _count_statements(
        db_engine, lambda: client.get(f"/api/v1/storage/cells/{cell['id']}/copies"))
    for _ in range(5):
        _copy(db_session, b.id, shelf_id=shelf["id"], cell_id=cell["id"])
    db_session.commit()
    grown = _count_statements(
        db_engine, lambda: client.get(f"/api/v1/storage/cells/{cell['id']}/copies"))
    assert grown == baseline, f"语句数随行数增长: {baseline} → {grown}"
