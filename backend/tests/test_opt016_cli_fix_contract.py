"""OPT-016：set-isbn / merge / verify 对真实后端的契约联调。

BookshelfClient._request 接到 TestClient（带 Bearer Token），验证：
- 补 ISBN 走 PATCH /books/{id}（books:write），ISBN 冲突为 409；
- 合并走 POST /books/{id}/merge（books:delete），无该 scope 为 403，副本被迁移、源书被删；
- verify 用真实 show 响应能识别 ISBN 缺失、疑似重复，并保持只读。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Book, BookCopy
from tests.test_bug166_167_168_auth import _agent_token

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "cli", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import batch_import_covers as bic  # noqa: E402
from bookshelf.client import BookshelfClient  # noqa: E402
from bookshelf.isbn import normalize_isbn  # noqa: E402


def _wire(monkeypatch, client: TestClient, token: str) -> BookshelfClient:
    def fake_request(self, method, path, *, timeout=None, **kwargs):
        resp = client.request(method, f"/api/v1{path}", params=kwargs.get("params"), json=kwargs.get("json"),
                              headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            raise RuntimeError(f"[HTTP {resp.status_code}] {resp.json().get('detail') or resp.text}")
        return resp.json()

    monkeypatch.setattr(BookshelfClient, "_request", fake_request)
    return BookshelfClient(base_url="http://testserver")


@pytest.fixture()
def two_books(db_session: Session):
    """同一本书的两条记录：一条只有书名（封面图），一条带 ISBN 但标题有空格差异（条码图）。"""
    a = Book(title="影响世界的专利")
    b = Book(title="影响世界的 专利", isbn13="9787536692930")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(BookCopy(book_id=b.id, copy_type="physical", status="in_shelf", location="书房"))
    db_session.commit()
    return {"a": a.id, "b": b.id}


def test_set_isbn_fill_via_real_patch(client, monkeypatch, db_session, two_books):
    _, token = _agent_token(client, ["books:read", "books:write"])
    cli = _wire(monkeypatch, client, token)
    assert normalize_isbn("9787020002207")
    cli.update_book_isbn(two_books["a"], "9787020002207")
    shown = cli.show(two_books["a"])["data"]
    assert shown["isbn13"] == "9787020002207"

    # 若原书同时残留旧 isbn10，通过 CLI 替换 ISBN 时应连同清空旧 isbn10 且校验 expected_isbn13
    row = db_session.get(Book, two_books["a"])
    row.isbn10 = "702000220X"
    db_session.commit()
    cli.update_book_isbn(two_books["a"], "9787111128069", expected_isbn13="9787020002207")
    shown_after = cli.show(two_books["a"])["data"]
    assert shown_after["isbn13"] == "9787111128069" and shown_after["isbn10"] is None



def test_set_isbn_conflict_is_409(client, monkeypatch, two_books):
    _, token = _agent_token(client, ["books:read", "books:write"])
    cli = _wire(monkeypatch, client, token)
    with pytest.raises(RuntimeError, match=r"\[HTTP 409\]"):
        cli.update_book_isbn(two_books["a"], "9787536692930")  # 已被 b 占用


def test_merge_requires_books_delete_and_moves_copies(client, monkeypatch, two_books):
    _, weak = _agent_token(client, ["books:read", "books:write"])
    with pytest.raises(RuntimeError, match=r"\[HTTP 403\]"):
        _wire(monkeypatch, client, weak).merge_books(two_books["a"], two_books["b"])

    _, strong = _agent_token(client, ["books:read", "books:write", "books:delete"])
    cli = _wire(monkeypatch, client, strong)
    result = cli.merge_books(two_books["a"], two_books["b"])["data"]
    assert result["migrated"]["copies"] == 1
    target = cli.show(two_books["a"])["data"]
    assert target["isbn13"] == "9787536692930" and len(target["copies"]) == 1
    with pytest.raises(RuntimeError, match=r"\[HTTP 404\]"):
        cli.show(two_books["b"])


def test_verify_against_real_responses_is_read_only(client, monkeypatch, tmp_path, two_books):
    """TST-017 场景：两条记录无公共键被建成两本——verify 抓出疑似重复，并给出可执行的 merge 建议。"""
    _, token = _agent_token(client, ["books:read"])  # 只读 token 足够：verify 不写库
    cli = _wire(monkeypatch, client, token)
    covers = tmp_path / "covers"
    covers.mkdir()
    for n in ("cover.jpg", "barcode.jpg"):
        (covers / n).write_bytes(b"x")
    manifest = tmp_path / "m.json"
    entries = [
        {"photo_id": "p0001", "file": "cover.jpg", "role": "cover", "title": "影响世界的专利", "status": "imported",
         "result": {"outcome": "created", "book_id": two_books["a"]}},
        {"photo_id": "p0002", "file": "barcode.jpg", "role": "barcode", "isbn": "9787536692930", "status": "imported",
         "result": {"outcome": "created", "book_id": two_books["b"]}},
    ]
    manifest.write_text(json.dumps({"version": 2, "source_dir": str(covers), "entries": entries},
                                   ensure_ascii=False), encoding="utf-8")
    before = manifest.read_bytes()
    out = tmp_path / "v.json"
    args = SimpleNamespace(manifest=str(manifest), out=str(out), min_confidence=None)
    rc = bic.cmd_verify(args, client=cli)

    assert rc == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["read_only"] is True
    assert [sorted(d["book_ids"]) for d in report["duplicates"]] == [sorted(two_books.values())]
    assert manifest.read_bytes() == before
    # 两书当前都无异常的内容不符；问题只在“疑似重复”
    assert all(not r["issues"] for r in report["entries"])
