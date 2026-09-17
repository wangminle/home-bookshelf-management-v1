"""CHK-107 回归：全系统复查确认 bug 的修复锁定。

覆盖：
- 无 ISBN 同名书封面不互相覆盖 / 删书换封面不误删他人封面（download_cover /
  delete_book / set_book_cover 的引用检查）
- MCP parse_cover_uri 拒绝非 ASCII 数字（RESOURCE_URI_INVALID 而非 INTERNAL_ERROR）
- /health 携带 frontend_version/app_version（doctor 漂移检查依据）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.services import storage as storage_mod
from app.services.storage import download_cover


# --- 封面共享：download_cover 不覆盖已存在目标 -------------------------------


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, n: int) -> bytes:
        d, self._data = self._data[:n], self._data[n:]
        return d

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeOpener:
    def __init__(self, data: bytes):
        self._data = data

    def open(self, req, timeout=None):
        return _FakeResp(self._data)


def test_download_cover_does_not_overwrite_existing_dest(tmp_path: Path, monkeypatch):
    """目标文件已存在（另一本无 ISBN 同名书在用）时改用唯一文件名，内容不被替换。"""
    data_dir = tmp_path / "data"
    covers = data_dir / "covers"
    covers.mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(storage_mod, "_is_safe_url", lambda url: (True, "203.0.113.10"))
    monkeypatch.setattr(storage_mod, "_SAFE_OPENER", _FakeOpener(b"new-book-cover"))

    existing = covers / "same_title.jpg"
    existing.write_bytes(b"old-book-cover")

    rel = download_cover("http://example.com/c.jpg", "same_title")
    assert rel is not None
    # 新封面落到带 uuid 后缀的唯一文件，不占用已有文件
    assert rel != "covers/same_title.jpg"
    assert (data_dir / rel).read_bytes() == b"new-book-cover"
    # 已有文件（另一本书正在引用）内容原样保留
    assert existing.read_bytes() == b"old-book-cover"


# --- 封面共享：删书 / 换封面先查引用 ------------------------------------------


def _mk_book(db_session, title: str, cover_rel: str | None):
    from app.models import Book
    from app.utils.book_helpers import normalize_title

    book = Book(title=title, normalized_title=normalize_title(title), source="manual")
    if cover_rel:
        book.cover_path = cover_rel
    db_session.add(book)
    db_session.commit()
    return book


def test_delete_book_keeps_cover_referenced_by_other_book(client, db_session):
    """两本书共享同一 cover_path 时，删除其一不得删掉另一本正在用的封面文件。"""
    from app.services.books import delete_book

    # client fixture 走真实 settings.data_dir，封面放到该目录下
    real_cover = settings.data_dir / "covers" / "shared_delete.jpg"
    real_cover.parent.mkdir(parents=True, exist_ok=True)
    real_cover.write_bytes(b"shared")

    b1 = _mk_book(db_session, "共享封面删除甲", "covers/shared_delete.jpg")
    b2 = _mk_book(db_session, "共享封面删除乙", "covers/shared_delete.jpg")

    delete_book(db_session, b1.id)
    # b2 仍引用该文件，文件必须保留
    assert real_cover.exists()

    delete_book(db_session, b2.id)
    # 最后一个引用者删除后，文件清理
    assert not real_cover.exists()


def test_set_book_cover_keeps_old_cover_referenced_by_other_book(client, db_session):
    """换封面时，旧封面若被其它书引用则不删除。"""
    from app.models import Book
    from app.services.books import set_book_cover

    real_old = settings.data_dir / "covers" / "shared_replace.jpg"
    real_old.parent.mkdir(parents=True, exist_ok=True)
    real_old.write_bytes(b"old-shared")

    b1 = _mk_book(db_session, "共享封面替换甲", "covers/shared_replace.jpg")
    b2 = _mk_book(db_session, "共享封面替换乙", "covers/shared_replace.jpg")

    real_new = settings.data_dir / "covers" / "new_unique_cover.jpg"
    real_new.write_bytes(b"new")

    set_book_cover(db_session, b1.id, "covers/new_unique_cover.jpg")
    # b2 仍引用旧封面，旧文件必须保留
    assert real_old.exists()
    assert db_session.get(Book, b2.id).cover_path == "covers/shared_replace.jpg"


# --- MCP parse_cover_uri：非 ASCII 数字 ---------------------------------------


def test_parse_cover_uri_rejects_non_ascii_digits():
    from app.mcp_server.tools.catalog import ToolError, parse_cover_uri

    for uri in ("bookshelf://covers/１２３", "bookshelf://covers/2²", "bookshelf://covers/٠١٢"):
        with pytest.raises(ToolError) as exc_info:
            parse_cover_uri(uri)
        assert exc_info.value.code == "RESOURCE_URI_INVALID"
    # 合法 ASCII 路径不受影响
    assert parse_cover_uri("bookshelf://covers/42") == 42


# --- /health 携带前端版本态势（doctor 漂移检查依据） ---------------------------


def test_health_out_carries_frontend_version_fields():
    """/health 响应模型含 app_version/frontend_version 字段（持有 Token 的 doctor 依赖）。"""
    from app.schemas.book import HealthOut

    fields = HealthOut.model_fields
    assert "app_version" in fields
    assert "frontend_version" in fields
