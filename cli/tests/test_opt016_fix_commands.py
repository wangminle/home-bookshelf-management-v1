"""OPT-016：给已有书补 ISBN、合并两条记录的 CLI 命令（含安全默认值）。

后端 PATCH /books/{id}（books:write）与 POST /books/{id}/merge（books:delete）已存在，
这里只提供带保护的命令：set-isbn 默认只补缺失、不覆盖；merge 默认只预览，--yes 才执行。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from bookshelf.client import BookshelfClient
from bookshelf.isbn import normalize_isbn
from bookshelf.main import app

runner = CliRunner()


def book(bid, title="三体", isbn13=None, copies=1):
    return {"ok": True, "data": {"id": bid, "title": title, "isbn13": isbn13,
                                 "copies": [{"id": i} for i in range(copies)]}}


def invoke(mock_client, *args):
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, list(args))
    return result


# ── ISBN 规范化 ──

def test_normalize_isbn_accepts_13_and_10_and_separators():
    assert normalize_isbn("978-7-5366-9293-0") == "9787536692930"
    assert normalize_isbn("702000220X") == "9787020002207"
    assert normalize_isbn(" 7-02-000220-x ") == "9787020002207"


def test_normalize_isbn_rejects_bad_checksum_or_shape():
    for bad in ("9787536692931", "123", "", "abcdefghijklm", "702000220Y"):
        assert normalize_isbn(bad) is None


def test_normalize_isbn_rejects_unicode_digits_without_crashing():
    assert normalize_isbn("978753669293²") is None
    assert normalize_isbn("９７８７５３６６９２９３０") is None


# ── set-isbn ──

def test_set_isbn_fills_missing_isbn():
    c = MagicMock()
    c.show.return_value = book(3, isbn13=None)
    c.update_book_isbn.return_value = {"ok": True, "data": {"id": 3, "isbn13": "9787536692930"}}
    r = invoke(c, "set-isbn", "--id", "3", "--isbn", "978-7-5366-9293-0")
    assert r.exit_code == 0, r.output
    c.update_book_isbn.assert_called_once_with(3, "9787536692930", expected_isbn13=None)
    doc = json.loads(r.output)
    assert doc["ok"] is True and doc["data"]["action"] == "set" and doc["data"]["isbn13"] == "9787536692930"


def test_set_isbn_same_value_is_noop_even_in_isbn10_form():
    c = MagicMock()
    c.show.return_value = book(3, isbn13="9787020002207")
    r = invoke(c, "set-isbn", "--id", "3", "--isbn", "702000220X")
    assert r.exit_code == 0
    c.update_book_isbn.assert_not_called()
    assert json.loads(r.output)["data"]["action"] == "unchanged"


def test_set_isbn_refuses_to_overwrite_different_isbn():
    c = MagicMock()
    c.show.return_value = book(3, isbn13="9787020002207")
    r = invoke(c, "set-isbn", "--id", "3", "--isbn", "9787536692930")
    assert r.exit_code == 1
    assert "已有 ISBN" in json.loads(r.output)["error"] and "--replace" in json.loads(r.output)["error"]
    c.update_book_isbn.assert_not_called()


def test_set_isbn_replace_overwrites():
    c = MagicMock()
    c.show.return_value = book(3, isbn13="9787020002207")
    c.update_book_isbn.return_value = {"ok": True, "data": {"id": 3}}
    r = invoke(c, "set-isbn", "--id", "3", "--isbn", "9787536692930", "--replace")
    assert r.exit_code == 0
    c.update_book_isbn.assert_called_once_with(3, "9787536692930", expected_isbn13="9787020002207")
    assert json.loads(r.output)["data"]["action"] == "replaced"


def test_set_isbn_treats_isbn10_as_existing_identity():
    c = MagicMock()
    shown = book(3, isbn13=None)
    shown["data"]["isbn10"] = "702000220X"
    c.show.return_value = shown
    r = invoke(c, "set-isbn", "--id", "3", "--isbn", "9787536692930")
    assert r.exit_code == 1
    assert "--replace" in json.loads(r.output)["error"]
    c.update_book_isbn.assert_not_called()

    same = invoke(c, "set-isbn", "--id", "3", "--isbn", "702000220X")
    assert same.exit_code == 0
    assert json.loads(same.output)["data"]["action"] == "unchanged"
    c.update_book_isbn.assert_not_called()


def test_set_isbn_invalid_isbn_rejected_before_any_request():
    c = MagicMock()
    r = invoke(c, "set-isbn", "--id", "3", "--isbn", "9787536692931")
    assert r.exit_code == 1
    assert "校验" in json.loads(r.output)["error"]
    c.show.assert_not_called()
    c.update_book_isbn.assert_not_called()


def test_set_isbn_conflict_from_backend_is_clean_error():
    c = MagicMock()
    c.show.return_value = book(3, isbn13=None)
    c.update_book_isbn.side_effect = RuntimeError("[HTTP 409] 书籍已存在（ISBN 冲突）")
    r = invoke(c, "set-isbn", "--id", "3", "--isbn", "9787536692930")
    assert r.exit_code == 1
    assert "[HTTP 409]" in json.loads(r.output)["error"]


# ── merge ──

def test_merge_defaults_to_preview_and_never_calls_merge():
    c = MagicMock()
    c.show.side_effect = lambda bid: {1: book(1, "三体", None, copies=1), 2: book(2, "三体", "9787536692930", copies=2)}[bid]
    r = invoke(c, "merge", "--target", "1", "--source", "2")
    assert r.exit_code == 0, r.output
    c.merge_books.assert_not_called()
    d = json.loads(r.output)["data"]
    assert d["executed"] is False
    assert d["target"]["id"] == 1 and d["source"]["id"] == 2
    assert d["source"]["copies"] == 2 and d["target"]["copies"] == 1
    assert "--yes" in d["hint"] and "删除" in d["hint"]


def test_merge_yes_executes_once():
    c = MagicMock()
    c.show.side_effect = lambda bid: {1: book(1), 2: book(2)}[bid]
    c.merge_books.return_value = {"ok": True, "data": {"id": 1, "message": "已将《三体》合并入《三体》"}}
    r = invoke(c, "merge", "--target", "1", "--source", "2", "--yes")
    assert r.exit_code == 0, r.output
    c.merge_books.assert_called_once_with(1, 2, force=False, expected_target_isbn13="", expected_source_isbn13="")
    d = json.loads(r.output)["data"]
    assert d["executed"] is True and d["result"]["id"] == 1


def test_merge_refuses_when_both_have_different_isbn_unless_force():
    c = MagicMock()
    c.show.side_effect = lambda bid: {1: book(1, isbn13="9787020002207"), 2: book(2, isbn13="9787536692930")}[bid]
    r = invoke(c, "merge", "--target", "1", "--source", "2", "--yes")
    assert r.exit_code == 1
    assert "ISBN 不同" in json.loads(r.output)["error"]
    c.merge_books.assert_not_called()

    c.merge_books.return_value = {"ok": True, "data": {"id": 1}}
    r = invoke(c, "merge", "--target", "1", "--source", "2", "--yes", "--force")
    assert r.exit_code == 0
    c.merge_books.assert_called_once_with(1, 2, force=True, expected_target_isbn13="9787020002207", expected_source_isbn13="9787536692930")


def test_merge_same_id_rejected():
    c = MagicMock()
    r = invoke(c, "merge", "--target", "5", "--source", "5", "--yes")
    assert r.exit_code == 1
    assert "不能" in json.loads(r.output)["error"]
    c.show.assert_not_called()
    c.merge_books.assert_not_called()


def test_merge_missing_source_is_clean_error_and_no_merge():
    def show(bid):
        if bid == 1:
            return book(1)
        raise RuntimeError("[HTTP 404] 书籍 ID 2 不存在")

    c = MagicMock()
    c.show.side_effect = show
    r = invoke(c, "merge", "--target", "1", "--source", "2", "--yes")
    assert r.exit_code == 1
    assert "[HTTP 404]" in json.loads(r.output)["error"]
    c.merge_books.assert_not_called()


# ── 客户端：请求形态 ──

def test_client_request_shapes():
    c = BookshelfClient(base_url="http://x")
    with patch.object(BookshelfClient, "_request", return_value={"ok": True}) as req:
        c.update_book_isbn(3, "9787536692930")
        c.merge_books(3, 4)
    assert req.call_args_list[0].args == ("PATCH", "/books/3")
    assert req.call_args_list[0].kwargs == {
        "json": {"isbn13": "9787536692930", "isbn10": None, "expected_isbn13": None}}
    assert req.call_args_list[1].args == ("POST", "/books/3/merge")
    assert req.call_args_list[1].kwargs == {"params": {"source_id": 4}}
