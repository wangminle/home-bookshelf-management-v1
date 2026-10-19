"""LOC-18：CLI 只读定位适配（locate / storage 只读子命令）。

契约（设计文档 §9、§10.3）：
- 精确位置需要 locations:read；无该 Scope 时响应根本不含 placement 键，CLI 不得推断；
- 外借等非在架状态显示归属位置，不推断“当前在架”；
- 无副本/无位置如实说明，不推断房间或格子；
- --location 仍是原样文字；storage 命令组只有 GET，不暴露任何结构化写入。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from bookshelf.client import BookshelfClient
from bookshelf.location import summarize_locations
from bookshelf.main import app

runner = CliRunner()

DISPLAY = "书房 A 号书架第 3 层左格"


def copy(cid=1, *, status="in_shelf", copy_type="physical", placement=True, display=DISPLAY,
         legacy=None, granted=True):
    base = {"id": cid, "book_id": 7, "copy_type": copy_type, "status": status, "location": legacy}
    if granted:
        base["placement_version"] = 2
        if placement:
            base["placement"] = {"shelf_id": 3, "cell_id": 9, "version": 2}
            base["location_display"] = display
        else:
            base["placement"] = None
            base["location_display"] = legacy
    return base


def book(*copies, title="影响世界的专利"):
    return {"id": 7, "title": title, "copies": list(copies)}


# ── 纯函数：summarize_locations ──

def test_lent_out_shows_home_location_not_on_shelf():
    s = summarize_locations(book(copy(status="lent_out")))
    assert s["location_access"] == "granted"
    c = s["copies"][0]
    assert c["position_state"] == "structured"
    assert c["location_display"] == DISPLAY
    assert c["on_shelf"] is False
    assert f"归属位置为{DISPLAY}" in s["summary"]
    assert "外借" in s["summary"] and "当前不在架" in s["summary"]


def test_in_shelf_with_structured_position():
    s = summarize_locations(book(copy(status="in_shelf")))
    c = s["copies"][0]
    assert c["on_shelf"] is True
    assert DISPLAY in s["summary"]
    assert "不在架" not in s["summary"]


def test_no_copies_states_missing_and_infers_nothing():
    s = summarize_locations(book())
    assert s["copies"] == []
    assert s["location_access"] == "unknown"
    assert "尚无副本" in s["summary"]
    assert "房" not in s["summary"].replace("尚无副本登记", "")


def test_physical_copy_without_any_position():
    s = summarize_locations(book(copy(placement=False, legacy=None)))
    c = s["copies"][0]
    assert c["position_state"] == "none"
    assert c["location_display"] is None
    assert "未登记位置" in s["summary"]


def test_legacy_text_is_reported_as_legacy_not_structured():
    s = summarize_locations(book(copy(placement=False, legacy="客厅书柜")))
    c = s["copies"][0]
    assert c["position_state"] == "legacy_text"
    assert c["legacy_location"] == "客厅书柜"
    assert c["placement"] is None
    assert "旧文字位置" in s["summary"] and "客厅书柜" in s["summary"]
    assert "未登记结构化位置" in s["summary"]


def test_without_locations_read_scope_no_inference():
    s = summarize_locations(book(copy(granted=False, legacy="客厅书柜")))
    assert s["location_access"] == "not_granted"
    c = s["copies"][0]
    assert c["position_state"] == "unknown"
    assert c["placement"] is None and c["location_display"] is None
    assert "locations:read" in s["summary"]
    # 未授权时旧位置也不能作为 Agent 转述依据。
    assert c["legacy_location"] is None
    assert "客厅书柜" not in s["summary"]


def test_digital_copy_has_no_physical_position():
    s = summarize_locations(book(copy(1, copy_type="digital", placement=False, legacy=None), copy(2)))
    kinds = {c["copy_id"]: c["position_state"] for c in s["copies"]}
    assert kinds == {1: "not_applicable", 2: "structured"}
    assert "1 册实体" in s["summary"] and "电子副本" in s["summary"]


def test_only_digital_copies_do_not_claim_physical_presence():
    s = summarize_locations(book(copy(1, copy_type="digital", placement=False, legacy=None)))
    assert "0 册实体" in s["summary"] or "没有实体" in s["summary"]


def test_two_physical_copies_listed_separately():
    s = summarize_locations(book(copy(1, status="lent_out"), copy(2, display="卧室 B 架第 1 层右格")))
    assert [c["copy_id"] for c in s["copies"]] == [1, 2]
    assert "2 册实体" in s["summary"]
    assert "卧室 B 架第 1 层右格" in s["summary"]


def test_other_non_shelf_statuses_not_on_shelf():
    for st in ("lost", "damaged", "storage", "discarded"):
        s = summarize_locations(book(copy(status=st)))
        assert s["copies"][0]["on_shelf"] is False, st
        assert "不在架" in s["summary"], st


# ── CLI 命令 ──

def test_locate_command_json():
    mock_client = MagicMock()
    mock_client.show.return_value = {"ok": True, "data": book(copy(status="lent_out"))}
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["locate", "--id", "7", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["ok"] is True
    assert doc["data"]["book_id"] == 7 and doc["data"]["title"] == "影响世界的专利"
    assert doc["data"]["copies"][0]["on_shelf"] is False
    mock_client.show.assert_called_once_with(7)


def test_locate_command_text_prints_summary():
    mock_client = MagicMock()
    mock_client.show.return_value = {"ok": True, "data": book(copy(status="lent_out"))}
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["locate", "--id", "7", "--no-json"])
    assert result.exit_code == 0
    assert f"归属位置为{DISPLAY}" in result.output


def test_locate_clean_error():
    mock_client = MagicMock()
    mock_client.show.side_effect = RuntimeError("[HTTP 404] 书籍 ID 7 不存在")
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["locate", "--id", "7", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output) == {"ok": False, "error": "[HTTP 404] 书籍 ID 7 不存在"}


def test_storage_group_exposes_read_only_subcommands_only():
    result = runner.invoke(app, ["storage", "--help"])
    assert result.exit_code == 0
    names = {c.name for c in app.registered_groups[[g.name for g in app.registered_groups].index("storage")].typer_instance.registered_commands}
    assert names == {"rooms", "shelves", "shelf", "cell", "unlocated"}


def test_storage_commands_call_client_get_methods():
    mock_client = MagicMock()
    for name in ("storage_rooms", "storage_shelves", "storage_shelf", "storage_cell_copies", "storage_unlocated"):
        getattr(mock_client, name).return_value = {"ok": True, "data": {"items": [], "total": 0}}
    with patch("bookshelf.main.client", mock_client):
        assert runner.invoke(app, ["storage", "rooms", "--limit", "5", "--offset", "10"]).exit_code == 0
        assert runner.invoke(app, ["storage", "shelves", "--room-id", "2"]).exit_code == 0
        assert runner.invoke(app, ["storage", "shelf", "--id", "3"]).exit_code == 0
        assert runner.invoke(app, ["storage", "cell", "--id", "9"]).exit_code == 0
        assert runner.invoke(app, ["storage", "unlocated"]).exit_code == 0
    mock_client.storage_rooms.assert_called_once_with(include_archived=False, limit=5, offset=10)
    mock_client.storage_shelves.assert_called_once_with(room_id=2, include_archived=False, limit=20, offset=0)
    mock_client.storage_shelf.assert_called_once_with(3, include_archived=False)
    mock_client.storage_cell_copies.assert_called_once_with(9, limit=20, offset=0)
    mock_client.storage_unlocated.assert_called_once_with(limit=20, offset=0)


def test_storage_403_adds_scope_hint():
    mock_client = MagicMock()
    mock_client.storage_rooms.side_effect = RuntimeError("[HTTP 403] 缺少权限")
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["storage", "rooms", "--json"])
    assert result.exit_code == 1
    err = json.loads(result.output)["error"]
    assert "[HTTP 403]" in err and "locations:read" in err


# ── 客户端：只读 GET 路径 ──

def test_client_storage_methods_issue_get_requests():
    c = BookshelfClient(base_url="http://x")
    with patch.object(BookshelfClient, "_request", return_value={"ok": True}) as req:
        c.storage_rooms(include_archived=True, limit=5, offset=1)
        c.storage_shelves(room_id=2, limit=7, offset=0)
        c.storage_shelf(3)
        c.storage_cell_copies(9, limit=3, offset=6)
        c.storage_unlocated(limit=4, offset=8)
    calls = [(a.args, a.kwargs) for a in req.call_args_list]
    assert calls[0] == (("GET", "/storage/rooms"), {"params": {"include_archived": "true", "limit": 5, "offset": 1}})
    assert calls[1] == (("GET", "/storage/shelves"), {"params": {"room_id": 2, "limit": 7, "offset": 0}})
    assert calls[2] == (("GET", "/storage/shelves/3"), {"params": {}})
    assert calls[3] == (("GET", "/storage/cells/9/copies"), {"params": {"limit": 3, "offset": 6}})
    assert calls[4] == (("GET", "/storage/unlocated"), {"params": {"limit": 4, "offset": 8}})
    assert all(a.args[0] == "GET" for a in req.call_args_list)


# ── 兼容：--location 仍是原样文字 ──

def test_add_location_text_is_passed_through_verbatim():
    mock_client = MagicMock()
    mock_client.add.return_value = {"ok": True, "data": {"message": "ok"}}
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["add", "--title", "三体", "--location", "书房/A-3/左", "--json"])
    assert result.exit_code == 0
    assert mock_client.add.call_args.kwargs["location"] == "书房/A-3/左"
    for forbidden in ("shelf_id", "cell_id", "placement"):
        assert forbidden not in mock_client.add.call_args.kwargs
