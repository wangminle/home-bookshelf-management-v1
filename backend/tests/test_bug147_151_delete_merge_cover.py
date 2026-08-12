"""BUG-147 / BUG-148 / BUG-151：删除书籍、合并书籍、设置封面。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings


def _create_book(client, **kwargs) -> dict:
    """通过 intake/json 建一本书，返回 data。"""
    r = client.post("/api/v1/books/intake/json", json=kwargs)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


# --- BUG-147：DELETE /books/{id} -------------------------------------------------


def test_delete_book_removes_book_and_soft_relations(client, db_session):
    """删除书后：书行消失、附件/自定义字段软关联清掉、封面文件删除。"""
    from app.models import Attachment, Book, CustomField

    data = _create_book(client, title="待删书", author="作者")
    book_id = data["book"]["id"]

    # 挂一个附件和一个自定义字段（软关联，无外键）
    db_session.add(Attachment(entity_type="book", entity_id=book_id, attach_type="note", content_md="x"))
    db_session.add(
        CustomField(entity_type="book", entity_id=book_id, field_key="购入地", field_value="新华书店")
    )
    # 挂一个封面文件
    cover_file = settings.data_dir / "covers" / "todelete.jpg"
    cover_file.parent.mkdir(parents=True, exist_ok=True)
    cover_file.write_bytes(b"fake-cover")
    db_session.query(Book).filter(Book.id == book_id).update({"cover_path": "covers/todelete.jpg"})
    db_session.commit()

    r = client.delete(f"/api/v1/books/{book_id}")
    assert r.status_code == 200, r.text
    assert "已删除" in r.json()["data"]["message"]

    # 书行没了
    assert db_session.get(Book, book_id) is None
    # 软关联清掉
    assert db_session.query(Attachment).filter_by(entity_type="book", entity_id=book_id).count() == 0
    assert db_session.query(CustomField).filter_by(entity_type="book", entity_id=book_id).count() == 0
    # 封面文件删除
    assert not cover_file.exists()


def test_delete_nonexistent_book_returns_404(client):
    r = client.delete("/api/v1/books/99999")
    assert r.status_code == 404


def test_delete_book_cascades_hard_relations(client, db_session):
    """硬外键关联（副本/购买/进度/笔记）随书删除而清除。"""
    from app.models import BookCopy, ReadingNote, ReadingProgress

    data = _create_book(client, title="硬关联书", author="作者", location="书架1")
    book_id = data["book"]["id"]

    # intake 带 location 已建副本；再补一条进度和笔记
    from app.models import Member

    member = Member(name="测试成员", role="owner")
    db_session.add(member)
    db_session.flush()
    db_session.add(ReadingProgress(book_id=book_id, member_id=member.id, status="reading"))
    db_session.add(ReadingNote(book_id=book_id, member_id=member.id, content_md="笔记"))
    db_session.commit()
    assert db_session.query(BookCopy).filter_by(book_id=book_id).count() >= 1

    r = client.delete(f"/api/v1/books/{book_id}")
    assert r.status_code == 200, r.text

    assert db_session.query(BookCopy).filter_by(book_id=book_id).count() == 0
    assert db_session.query(ReadingProgress).filter_by(book_id=book_id).count() == 0
    assert db_session.query(ReadingNote).filter_by(book_id=book_id).count() == 0


# --- BUG-148：合并书籍 ----------------------------------------------------------


def test_merge_books_migrates_relations_and_deletes_source(client, db_session):
    """source 的副本/进度/笔记/附件/标签迁移到 target，source 被删除。"""
    from app.models import (
        Attachment,
        Book,
        BookCopy,
        BookTag,
        CustomField,
        Member,
        ReadingNote,
        ReadingProgress,
        Tag,
    )

    target_data = _create_book(client, title="目标书", author="作者A", location="书架1")
    source_data = _create_book(client, title="源书", author="作者B", location="书架2")
    target_id = target_data["book"]["id"]
    source_id = source_data["book"]["id"]

    member = Member(name="合并成员", role="owner")
    db_session.add(member)
    db_session.flush()
    # source 上的进度、笔记、附件、自定义字段、标签
    db_session.add(ReadingProgress(book_id=source_id, member_id=member.id, status="reading"))
    db_session.add(ReadingNote(book_id=source_id, member_id=member.id, content_md="源笔记"))
    db_session.add(Attachment(entity_type="book", entity_id=source_id, attach_type="note", content_md="源附件"))
    db_session.add(
        CustomField(entity_type="book", entity_id=source_id, field_key="来源", field_value="赠书")
    )
    tag = Tag(name="科幻")
    db_session.add(tag)
    db_session.flush()
    db_session.add(BookTag(book_id=source_id, tag_id=tag.id))
    db_session.commit()

    r = client.post(f"/api/v1/books/{target_id}/merge?source_id={source_id}")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    mig = d["migrated"]
    assert mig["copies"] == 1
    assert mig["progress"] == 1
    assert mig["notes"] == 1
    assert mig["attachments"] == 1
    assert mig["custom_fields"] == 1
    assert "科幻" in mig["tags"]

    # source 被删
    assert db_session.get(Book, source_id) is None
    # target 仍在，且关联迁过来了
    assert db_session.get(Book, target_id) is not None
    assert db_session.query(BookCopy).filter_by(book_id=target_id).count() == 2  # target 原有 1 + source 迁来 1
    assert db_session.query(ReadingProgress).filter_by(book_id=target_id).count() == 1
    assert db_session.query(ReadingNote).filter_by(book_id=target_id).count() == 1
    assert db_session.query(Attachment).filter_by(entity_type="book", entity_id=target_id).count() == 1
    assert db_session.query(CustomField).filter_by(entity_type="book", entity_id=target_id).count() == 1
    assert db_session.query(BookTag).filter_by(book_id=target_id).count() == 1


def test_merge_same_book_rejected(client):
    data = _create_book(client, title="同一本", author="作者")
    bid = data["book"]["id"]
    r = client.post(f"/api/v1/books/{bid}/merge?source_id={bid}")
    assert r.status_code == 400


def test_merge_progress_conflict_drops_source_row(client, db_session):
    """target 已有同成员进度时，source 的进度行被丢弃（保留 target）。"""
    from app.models import Member, ReadingProgress

    target_data = _create_book(client, title="T书", author="A")
    source_data = _create_book(client, title="S书", author="B")
    target_id = target_data["book"]["id"]
    source_id = source_data["book"]["id"]

    member = Member(name="冲突成员", role="owner")
    db_session.add(member)
    db_session.flush()
    db_session.add(ReadingProgress(book_id=target_id, member_id=member.id, status="finished"))
    db_session.add(ReadingProgress(book_id=source_id, member_id=member.id, status="reading"))
    db_session.commit()

    r = client.post(f"/api/v1/books/{target_id}/merge?source_id={source_id}")
    assert r.status_code == 200, r.text
    # target 上只剩 1 条进度，且是 finished（保留 target）
    progs = db_session.query(ReadingProgress).filter_by(book_id=target_id).all()
    assert len(progs) == 1
    assert progs[0].status == "finished"


def test_merge_backfills_target_missing_isbn_and_cover(client, db_session):
    """target 缺 ISBN/封面时用 source 回填。"""
    from app.models import Book

    # target 无 ISBN（纯书名入库），source 有 ISBN
    target_data = _create_book(client, title="无ISBN书", author="作者")
    source_data = _create_book(client, isbn="9780306406157")
    target_id = target_data["book"]["id"]
    source_id = source_data["book"]["id"]

    # 给 source 挂个封面文件
    cover = settings.data_dir / "covers" / "src_cover.jpg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"x")
    db_session.query(Book).filter(Book.id == source_id).update({"cover_path": "covers/src_cover.jpg"})
    db_session.commit()

    r = client.post(f"/api/v1/books/{target_id}/merge?source_id={source_id}")
    assert r.status_code == 200, r.text

    target = db_session.get(Book, target_id)
    assert target.isbn13 == "9780306406157"
    assert target.cover_path == "covers/src_cover.jpg"
    # source 封面归 target 后，source 删除时没误删该文件
    assert cover.exists()


# --- BUG-151：设置封面 ----------------------------------------------------------


def test_set_cover_uploads_and_sets_cover_path(client, db_session):
    from app.models import Book

    data = _create_book(client, title="无封面书", author="作者")
    book_id = data["book"]["id"]
    assert db_session.get(Book, book_id).cover_path is None

    import io

    files = {"image": ("cover.jpg", io.BytesIO(b"\xff\xd8\xff\xe0fakejpeg"), "image/jpeg")}
    r = client.post(f"/api/v1/books/{book_id}/cover", files=files)
    assert r.status_code == 200, r.text
    cover_path = r.json()["data"]["cover_path"]
    assert cover_path
    # 路径在 covers/ 目录下（兼容 Windows 反斜杠）
    rel = Path(cover_path)
    assert rel.parts[0] == "covers"
    # 文件落盘
    assert (settings.data_dir / cover_path).exists()
    # DB 字段更新
    assert db_session.get(Book, book_id).cover_path == cover_path


def test_set_cover_replaces_old_cover_file(client, db_session):
    """二次设封面后，旧封面文件被清理。"""
    from app.models import Book

    data = _create_book(client, title="换封面书", author="作者")
    book_id = data["book"]["id"]

    import io

    files1 = {"image": ("c1.jpg", io.BytesIO(b"first"), "image/jpeg")}
    r1 = client.post(f"/api/v1/books/{book_id}/cover", files=files1)
    assert r1.status_code == 200, r1.text
    old_path = settings.data_dir / r1.json()["data"]["cover_path"]
    assert old_path.exists()

    files2 = {"image": ("c2.jpg", io.BytesIO(b"second"), "image/jpeg")}
    r2 = client.post(f"/api/v1/books/{book_id}/cover", files=files2)
    assert r2.status_code == 200, r2.text
    # 旧文件没了（同名时被 overwrite 覆盖，或不同名时被 _delete_data_file 清理）
    new_path = settings.data_dir / r2.json()["data"]["cover_path"]
    assert new_path.exists()


def test_set_cover_nonexistent_book_returns_404(client):
    import io

    files = {"image": ("c.jpg", io.BytesIO(b"x"), "image/jpeg")}
    r = client.post("/api/v1/books/99999/cover", files=files)
    assert r.status_code == 404


# --- 回归：合并迁移阅读日志 / 封面引用安全 / 附件文件清理 / 封面文件名唯一 ---


def test_merge_books_migrates_reading_logs(client, db_session):
    """source 的 ReadingLog 必须迁移到 target，否则 ondelete=CASCADE 会丢失全部阅读历史。"""
    from app.models import Book, Member, ReadingLog

    target_data = _create_book(client, title="日志目标书", author="作者A")
    source_data = _create_book(client, title="日志源书", author="作者B")
    target_id = target_data["book"]["id"]
    source_id = source_data["book"]["id"]

    member = Member(name="日志成员", role="owner")
    db_session.add(member)
    db_session.flush()
    db_session.add(ReadingLog(book_id=source_id, member_id=member.id, log_date="2026-01-01", pages_read=10))
    db_session.add(ReadingLog(book_id=source_id, member_id=member.id, log_date="2026-01-02", pages_read=20))
    db_session.commit()

    r = client.post(f"/api/v1/books/{target_id}/merge?source_id={source_id}")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["migrated"]["logs"] == 2

    # source 被删，日志迁到 target，没有丢失
    assert db_session.get(Book, source_id) is None
    assert db_session.query(ReadingLog).filter_by(book_id=target_id).count() == 2
    assert db_session.query(ReadingLog).filter_by(book_id=source_id).count() == 0


def test_merge_preserves_cover_when_target_shares_same_path(client, db_session):
    """target 与 source 引用同一 cover_path 时，合并后不得误删该文件。"""
    from app.models import Book

    cover = settings.data_dir / "covers" / "shared_cover.jpg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"shared")

    target_data = _create_book(client, title="共享封面目标", author="A")
    source_data = _create_book(client, title="共享封面源", author="B")
    target_id = target_data["book"]["id"]
    source_id = source_data["book"]["id"]
    # 两本书都指向同一个封面文件
    db_session.query(Book).filter(Book.id == target_id).update({"cover_path": "covers/shared_cover.jpg"})
    db_session.query(Book).filter(Book.id == source_id).update({"cover_path": "covers/shared_cover.jpg"})
    db_session.commit()

    r = client.post(f"/api/v1/books/{target_id}/merge?source_id={source_id}")
    assert r.status_code == 200, r.text

    # target 仍引用该封面，文件必须存在
    assert db_session.get(Book, target_id).cover_path == "covers/shared_cover.jpg"
    assert cover.exists()


def test_delete_book_cleans_up_attachment_files(client, db_session):
    """删除书籍时，附件实体文件（Attachment.file_path）必须一并清理，不留孤儿。"""
    from app.models import Attachment

    data = _create_book(client, title="带附件书", author="作者")
    book_id = data["book"]["id"]

    att_file = settings.data_dir / "attachments" / "book_test_doc.pdf"
    att_file.parent.mkdir(parents=True, exist_ok=True)
    att_file.write_bytes(b"pdf-content")
    db_session.add(
        Attachment(
            entity_type="book",
            entity_id=book_id,
            attach_type="file",
            file_path="attachments/book_test_doc.pdf",
        )
    )
    db_session.commit()

    r = client.delete(f"/api/v1/books/{book_id}")
    assert r.status_code == 200, r.text

    # DB 行清掉
    assert db_session.query(Attachment).filter_by(entity_type="book", entity_id=book_id).count() == 0
    # 实体文件也清掉
    assert not att_file.exists()


def test_set_cover_same_title_different_books_no_collision(client, db_session):
    """两本无 ISBN 且同名的书各自设封面，文件名包含 book_id 不会互相覆盖。"""
    from app.models import Book
    from app.utils.book_helpers import normalize_title

    # intake 会按 normalized_title 去重，所以直接在 DB 建两本同名书模拟历史重复数据
    b1 = Book(title="同名书", normalized_title=normalize_title("同名书"), source="manual")
    b2 = Book(title="同名书", normalized_title=normalize_title("同名书"), source="manual")
    db_session.add_all([b1, b2])
    db_session.commit()
    id1, id2 = b1.id, b2.id
    assert id1 != id2

    import io

    r1 = client.post(
        f"/api/v1/books/{id1}/cover",
        files={"image": ("c.jpg", io.BytesIO(b"book1-cover"), "image/jpeg")},
    )
    r2 = client.post(
        f"/api/v1/books/{id2}/cover",
        files={"image": ("c.jpg", io.BytesIO(b"book2-cover"), "image/jpeg")},
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    path1 = r1.json()["data"]["cover_path"]
    path2 = r2.json()["data"]["cover_path"]
    # 两本书的封面路径不同（含各自 book_id）
    assert path1 != path2
    # 两本书的封面文件都存在且内容各自独立
    assert (settings.data_dir / path1).exists()
    assert (settings.data_dir / path2).exists()
    assert (settings.data_dir / path1).read_bytes() == b"book1-cover"
    assert (settings.data_dir / path2).read_bytes() == b"book2-cover"
    # DB 各自指向自己的封面
    assert db_session.get(Book, id1).cover_path == path1
    assert db_session.get(Book, id2).cover_path == path2
