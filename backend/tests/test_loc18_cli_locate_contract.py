"""LOC-18：CLI 只读定位适配对真实后端响应的契约联调。

CLI 单测只用手写 mock；这里把 BookshelfClient._request 接到 TestClient，
用真实序列化结果验证 locate 摘要与 storage 只读路径，并核对 Scope 隔离。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, ShelfCell, ShelfLayer, StorageRoom, StorageShelf
from tests.test_bug166_167_168_auth import _agent_token

CLI_DIR = Path(__file__).resolve().parents[2] / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from bookshelf.client import BookshelfClient  # noqa: E402
from bookshelf.location import summarize_locations  # noqa: E402


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    room = StorageRoom(code="RA", name="书房")
    db_session.add(room)
    db_session.flush()
    shelf = StorageShelf(room_id=room.id, code="SA1", name="A 号书架")
    db_session.add(shelf)
    db_session.flush()
    layer = ShelfLayer(shelf_id=shelf.id, label="第 3 层", sort_order=0)
    db_session.add(layer)
    db_session.flush()
    cell = ShelfCell(shelf_id=shelf.id, layer_id=layer.id, code="L", label="左格", sort_order=0)
    db_session.add(cell)
    db_session.flush()
    book = Book(title="影响世界的专利")
    db_session.add(book)
    db_session.flush()
    lent = BookCopy(book_id=book.id, copy_type="physical", status="lent_out",
                    placement_shelf_id=shelf.id, placement_cell_id=cell.id)
    legacy = BookCopy(book_id=book.id, copy_type="physical", location="客厅书柜")
    digital = BookCopy(book_id=book.id, copy_type="digital")
    empty_book = Book(title="没有副本的书")
    db_session.add_all([lent, legacy, digital, empty_book])
    db_session.commit()
    return {"book_id": book.id, "empty_book_id": empty_book.id, "room_id": room.id,
            "shelf_id": shelf.id, "cell_id": cell.id, "lent_id": lent.id, "legacy_id": legacy.id,
            "digital_id": digital.id}


def _wire(monkeypatch, client: TestClient, token: str) -> BookshelfClient:
    """让 CLI 客户端的 HTTP 请求走进 TestClient（带 Bearer Token）。"""

    def fake_request(self, method, path, *, timeout=None, **kwargs):
        resp = client.request(method, f"/api/v1{path}", params=kwargs.get("params"),
                              headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            raise RuntimeError(f"[HTTP {resp.status_code}] {resp.json().get('detail') or resp.text}")
        return resp.json()

    monkeypatch.setattr(BookshelfClient, "_request", fake_request)
    return BookshelfClient(base_url="http://testserver")


def test_locate_with_locations_read_on_real_response(client, monkeypatch, world):
    _, token = _agent_token(client, ["books:read", "locations:read"])
    cli = _wire(monkeypatch, client, token)
    summary = summarize_locations(cli.show(world["book_id"])["data"])

    assert summary["location_access"] == "granted"
    by_id = {c["copy_id"]: c for c in summary["copies"]}
    lent = by_id[world["lent_id"]]
    assert lent["position_state"] == "structured" and lent["on_shelf"] is False
    assert "书房" in lent["location_display"] and "第 3 层" in lent["location_display"]
    assert by_id[world["legacy_id"]]["position_state"] == "legacy_text"
    assert by_id[world["legacy_id"]]["legacy_location"] == "客厅书柜"
    assert by_id[world["digital_id"]]["position_state"] == "not_applicable"
    assert "当前不在架" in summary["summary"] and "2 册实体" in summary["summary"]


def test_locate_without_locations_read_does_not_leak_or_infer(client, monkeypatch, world):
    _, token = _agent_token(client, ["books:read"])
    cli = _wire(monkeypatch, client, token)
    raw = cli.show(world["book_id"])["data"]
    assert all("placement" not in c for c in raw["copies"])  # 后端已隔离
    summary = summarize_locations(raw)

    assert summary["location_access"] == "not_granted"
    assert all(c["position_state"] in ("unknown", "not_applicable") for c in summary["copies"])
    assert all(c["placement"] is None and c["location_display"] is None for c in summary["copies"])
    assert "书房" not in summary["summary"] and "locations:read" in summary["summary"]


def test_locate_book_without_copies(client, monkeypatch, world):
    _, token = _agent_token(client, ["books:read", "locations:read"])
    cli = _wire(monkeypatch, client, token)
    summary = summarize_locations(cli.show(world["empty_book_id"])["data"])
    assert summary["copies"] == [] and "尚无副本" in summary["summary"]


def test_storage_read_paths_work_with_locations_read(client, monkeypatch, world):
    _, token = _agent_token(client, ["locations:read"])
    cli = _wire(monkeypatch, client, token)
    rooms = cli.storage_rooms()["data"]
    assert any(r["id"] == world["room_id"] for r in rooms["items"])
    shelves = cli.storage_shelves(room_id=world["room_id"])["data"]
    assert any(s["id"] == world["shelf_id"] for s in shelves["items"])
    assert cli.storage_shelf(world["shelf_id"])["data"]
    cell = cli.storage_cell_copies(world["cell_id"])["data"]
    assert [i["copy_id"] for i in cell["items"]] == [world["lent_id"]]
    assert cli.storage_unlocated()["data"] is not None


def test_storage_read_paths_forbidden_without_locations_read(client, monkeypatch, world):
    _, token = _agent_token(client, ["books:read"])
    cli = _wire(monkeypatch, client, token)
    for call in (cli.storage_rooms, cli.storage_shelves, cli.storage_unlocated):
        with pytest.raises(RuntimeError, match=r"\[HTTP 403\]"):
            call()
    with pytest.raises(RuntimeError, match=r"\[HTTP 403\]"):
        cli.storage_shelf(world["shelf_id"])
    with pytest.raises(RuntimeError, match=r"\[HTTP 403\]"):
        cli.storage_cell_copies(world["cell_id"])

