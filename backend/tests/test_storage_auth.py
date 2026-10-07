"""LOC-05：位置鉴权与输出隔离测试。

冻结契约：
- Web 会话（Owner/Member）读副本 → 含 placement/placement_version/location_display 键；
- Bearer Token 按 locations:read 判定：无该 scope 时输出中三个键完全不出现
  （其余字段与改动前逐字节同结构），有该 scope 时回显登记原始值；
- 匿名按既有规则拒绝（/books/{id} 本不可匿名读）；
- MCP 目录工具输出字段集不漂移（MCP 不序列化副本位置）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.mcp_server.tools import catalog as mcp_catalog
from app.models import (
    Book,
    BookCopy,
    Member,
    ShelfCell,
    ShelfLayer,
    StorageRoom,
    StorageShelf,
)
from app.services import agent_access
from tests.test_bug166_167_168_auth import _agent_token

# 改动前副本输出的完整字段集（隔离时不得多一个键）
_LEGACY_COPY_KEYS = frozenset({
    "id", "book_id", "copy_type", "format", "location", "file_path",
    "owner_member_id", "acquire_type", "status", "condition",
    "created_at", "updated_at",
})

# MCP bookshelf_get_book 冻结输出字段（与 tests/mcp 契约一致）
_MCP_BOOK_KEYS = frozenset({
    "id", "title", "subtitle", "authors", "translators", "publisher",
    "publish_date", "edition", "language", "page_count", "category",
    "summary", "availability",
})


@pytest.fixture()
def placed_world(client: TestClient, db_session: Session) -> dict:
    """房间→书架→层→格 + 一本书（一个已定位副本、一个未定位副本）。"""
    room = StorageRoom(code="RA", name="书房")
    db_session.add(room)
    db_session.flush()
    shelf = StorageShelf(room_id=room.id, code="SA1", name="SA1 书架")
    db_session.add(shelf)
    db_session.flush()
    layer = ShelfLayer(shelf_id=shelf.id, label="第 1 层", sort_order=0)
    db_session.add(layer)
    db_session.flush()
    cell = ShelfCell(shelf_id=shelf.id, layer_id=layer.id, code="L", label="左格", sort_order=0)
    db_session.add(cell)
    db_session.flush()

    book = Book(title="位置隔离测试书")
    db_session.add(book)
    db_session.flush()
    placed = BookCopy(
        book_id=book.id, copy_type="physical",
        placement_shelf_id=shelf.id, placement_cell_id=cell.id,
    )
    # BUG-298：清除后的未定位副本也必须保留递增后的真实版本。
    unplaced = BookCopy(book_id=book.id, copy_type="physical", placement_version=3)
    db_session.add_all([placed, unplaced])
    db_session.commit()
    return {
        "book_id": book.id,
        "shelf_id": shelf.id,
        "cell_id": cell.id,
        "placed_copy_id": placed.id,
        "unplaced_copy_id": unplaced.id,
    }


def _member_web_client(db_session: Session) -> TestClient:
    """Member 角色的 Web 会话客户端（复用 client 夹具的 db 覆盖）。"""
    member = Member(name="位置成员", role="member")
    db_session.add(member)
    db_session.commit()
    session_token, _ = agent_access.create_web_session(db_session, member.id)
    c = TestClient(app)
    c.cookies.set("hbs_session", session_token, domain="testserver.local")
    return c


def _copy_by_id(data: dict, copy_id: int) -> dict:
    return next(c for c in data["copies"] if c["id"] == copy_id)


# ── ① Web 会话（Owner/Member）：含 placement 键，回显登记原始值 ──


def test_owner_web_sees_placement(client: TestClient, placed_world: dict) -> None:
    r = client.get(f"/api/v1/books/{placed_world['book_id']}")
    assert r.status_code == 200, r.text
    copies = r.json()["data"]["copies"]
    assert len(copies) == 2
    for c in copies:
        assert "placement" in c
        assert "location_display" in c
    placed = _copy_by_id(r.json()["data"], placed_world["placed_copy_id"])
    assert placed["placement"] == {
        "shelf_id": placed_world["shelf_id"],
        "cell_id": placed_world["cell_id"],
        "version": 1,
    }
    # 未定位副本：键存在、值为 None（LOC-08 才生成路径文本）
    unplaced = _copy_by_id(r.json()["data"], placed_world["unplaced_copy_id"])
    assert unplaced["placement"] is None
    assert placed["placement_version"] == placed["placement"]["version"]
    assert unplaced["placement_version"] == 3
    assert unplaced["location_display"] is None


def test_member_web_sees_placement(client: TestClient, db_session: Session, placed_world: dict) -> None:
    with _member_web_client(db_session) as c:
        r = c.get(f"/api/v1/books/{placed_world['book_id']}")
    assert r.status_code == 200, r.text
    placed = _copy_by_id(r.json()["data"], placed_world["placed_copy_id"])
    assert placed["placement"] == {
        "shelf_id": placed_world["shelf_id"],
        "cell_id": placed_world["cell_id"],
        "version": 1,
    }
    assert "location_display" in placed
    assert placed["placement_version"] == 1
    unplaced = _copy_by_id(r.json()["data"], placed_world["unplaced_copy_id"])
    assert unplaced["placement"] is None
    assert unplaced["placement_version"] == 3


# ── ②/③ Bearer Token：按 locations:read 隔离 ──


def test_token_without_locations_read_has_no_placement_keys(client: TestClient, placed_world: dict) -> None:
    _, token = _agent_token(client, ["books:read"])
    r = client.get(
        f"/api/v1/books/{placed_world['book_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    copies = r.json()["data"]["copies"]
    assert len(copies) == 2
    for c in copies:
        # 键完全不出现（非 None），其余字段集与改动前一致
        assert "placement" not in c
        assert "placement_version" not in c
        assert "location_display" not in c
        assert set(c.keys()) == set(_LEGACY_COPY_KEYS)


def test_token_with_locations_read_sees_placement(client: TestClient, placed_world: dict) -> None:
    _, token = _agent_token(client, ["books:read", "locations:read"])
    r = client.get(
        f"/api/v1/books/{placed_world['book_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    placed = _copy_by_id(r.json()["data"], placed_world["placed_copy_id"])
    assert placed["placement"] == {
        "shelf_id": placed_world["shelf_id"],
        "cell_id": placed_world["cell_id"],
        "version": 1,
    }
    assert "location_display" in placed
    assert placed["placement_version"] == 1
    unplaced = _copy_by_id(r.json()["data"], placed_world["unplaced_copy_id"])
    assert unplaced["placement"] is None
    assert unplaced["placement_version"] == 3


def test_copy_create_response_isolated_by_scope(client: TestClient, placed_world: dict) -> None:
    """POST /books/{book_id}/copies 响应同样按 locations:read 隔离。"""
    book_id = placed_world["book_id"]

    _, write_only = _agent_token(client, ["books:write"])
    r = client.post(
        f"/api/v1/books/{book_id}/copies",
        json={},
        headers={"Authorization": f"Bearer {write_only}"},
    )
    assert r.status_code == 201, r.text
    assert "placement" not in r.json()["data"]
    assert "placement_version" not in r.json()["data"]
    assert "location_display" not in r.json()["data"]

    _, with_locations = _agent_token(client, ["books:write", "locations:read"])
    r = client.post(
        f"/api/v1/books/{book_id}/copies",
        json={},
        headers={"Authorization": f"Bearer {with_locations}"},
    )
    assert r.status_code == 201, r.text
    assert "placement" in r.json()["data"]
    assert r.json()["data"]["placement_version"] == 1
    assert "location_display" in r.json()["data"]


# ── ④ 匿名：保持既有拒绝语义 ──


def test_anonymous_book_detail_rejected(anon_client: TestClient, placed_world: dict) -> None:
    r = anon_client.get(f"/api/v1/books/{placed_world['book_id']}")
    assert r.status_code in (401, 403), f"匿名读详情应保持拒绝，实际 {r.status_code}: {r.text}"


# ── ⑤ MCP 目录工具：无 locations:read 时输出结构不漂移 ──


def test_mcp_catalog_output_structure_unchanged(db_session: Session, placed_world: dict) -> None:
    detail = mcp_catalog.get_book(db_session, {"book_id": placed_world["book_id"]})
    assert set(detail.keys()) == set(_MCP_BOOK_KEYS)
    assert "placement" not in detail
    assert "placement_version" not in detail
    assert "location_display" not in detail

    result = mcp_catalog.search_books(db_session, {"query": "位置隔离"})
    assert result["count"] == 1
    for item in result["items"]:
        assert set(item.keys()) == set(_MCP_BOOK_KEYS)
        assert "placement" not in item
        assert "placement_version" not in item
        assert "location_display" not in item
