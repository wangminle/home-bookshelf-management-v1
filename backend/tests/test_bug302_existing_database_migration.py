"""BUG-302：已执行旧位置迁移的数据库也必须获得照片 ID 防复用保护。"""
from __future__ import annotations

import shutil
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Member, StorageRoom, StorageShelf
from app.models.base import create_engine_from_url
from app.models.storage import ShelfPhoto
from app.services import storage_photos
from app.services.storage_tx import NotFound

_PREVIOUS = "l3c4d5e6f7a8"


@pytest.fixture
def migration_db(tmp_path, monkeypatch):
    """复制迁移模拟原 l3 结构，不依赖 Git，也不改项目迁移或真实数据库。"""
    backend = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "alembic"
    shutil.copytree(backend / "alembic", scripts,
                    ignore=shutil.ignore_patterns("__pycache__"))
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(scripts))
    old = scripts / "versions/l3c4d5e6f7a8_storage_locations.py"
    original = old.read_text()
    assert original.count("sqlite_autoincrement=True,") == 1
    old.write_text(original.replace("sqlite_autoincrement=True,", ""))
    command.upgrade(cfg, _PREVIOUS)
    # 恢复修订版文件后继续升级：不能靠重新执行旧 revision 来修复旧库。
    old.write_text(original)
    engine = create_engine_from_url(url)
    with engine.begin() as conn:
        assert "AUTOINCREMENT" not in conn.scalar(text(
            "SELECT sql FROM sqlite_master WHERE name='shelf_photos'"))
    try:
        yield cfg, engine
    finally:
        engine.dispose()


def _seed(session):
    owner = Member(name="迁移测试 Owner", role="owner")
    room = StorageRoom(code="migration-room", name="书房")
    session.add_all([owner, room])
    session.flush()
    shelf = StorageShelf(room_id=room.id, code="migration-shelf", name="书架")
    session.add(shelf)
    session.commit()
    return shelf.id, owner.id


def _upload(session, shelf_id, owner_id, key):
    image = BytesIO()
    Image.new("RGB", (8, 8), (120, 30, 200)).save(image, "JPEG")
    return storage_photos.upload_photo(
        session, shelf_id, data=image.getvalue(), caption="迁移前照片",
        is_primary=False, idempotency_key=key, operator_member_id=owner_id,
    )["photo"]


def test_upgrade_existing_table_preserves_photos_and_constraints(migration_db):
    cfg, engine = migration_db
    with Session(engine) as session:
        shelf_id, owner_id = _seed(session)
        old = _upload(session, shelf_id, owner_id, "before-upgrade")
        before = session.execute(text("SELECT * FROM shelf_photos")).mappings().all()
        path = settings.data_dir / session.get(ShelfPhoto, old["id"]).relative_path
        image = path.read_bytes()
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert "AUTOINCREMENT" in conn.scalar(text(
            "SELECT sql FROM sqlite_master WHERE name='shelf_photos'"))
        assert conn.execute(text("SELECT * FROM shelf_photos")).mappings().all() == before
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        indexes = {row["name"]: row for row in inspect(conn).get_indexes("shelf_photos")}
        assert "ix_shelf_photos_shelf_id" in indexes
        assert indexes["uq_shelf_photos_primary"]["unique"]
        assert "is_primary = 1" in str(indexes["uq_shelf_photos_primary"]["dialect_options"]["sqlite_where"])
    assert path.read_bytes() == image
    with Session(engine) as session:
        replacement = _upload(session, shelf_id, owner_id, "after-upgrade")
        storage_photos.delete_photo(session, replacement["id"], version=1,
                                    operator_member_id=owner_id)
        new = _upload(session, shelf_id, owner_id, "after-delete")
        assert new["id"] > replacement["id"]
        with pytest.raises(NotFound):
            storage_photos.delete_photo(session, replacement["id"], version=1,
                                        operator_member_id=owner_id)
        assert session.get(ShelfPhoto, new["id"]) is not None
    # 迁移后部分唯一索引与外键都必须仍实际约束写入。
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO shelf_photos (shelf_id, relative_path, mime_type, width, height, is_primary) "
                              "VALUES (:shelf, 'duplicate.jpg', 'image/jpeg', 8, 8, 1)"), {"shelf": shelf_id})
        conn.rollback()
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO shelf_photos (shelf_id, relative_path, mime_type, width, height) "
                              "VALUES (999999, 'invalid.jpg', 'image/jpeg', 8, 8)"))
        conn.rollback()


@pytest.mark.parametrize("history", ["receipts", "audit", "both"])
def test_upgrade_reserves_deleted_ids_from_history(migration_db, history):
    """升级时照片表已空，也不能复用回执/审计里仍能查到的旧 ID。"""
    cfg, engine = migration_db
    with Session(engine) as session:
        shelf_id, owner_id = _seed(session)
        old = _upload(session, shelf_id, owner_id, "deleted-before-upgrade")
        storage_photos.delete_photo(session, old["id"], version=1,
                                    idempotency_key="delete-before-upgrade",
                                    operator_member_id=owner_id)
        if history == "receipts":
            session.execute(text("DELETE FROM operation_logs WHERE action LIKE 'storage.photo.%'"))
        elif history == "audit":
            session.execute(text("DELETE FROM location_operations"))
        session.commit()
        receipts = session.execute(text("SELECT * FROM location_operations")).all()
        logs = session.execute(text("SELECT * FROM operation_logs")).all()
    command.upgrade(cfg, "head")
    with Session(engine) as session:
        assert session.execute(text("SELECT * FROM location_operations")).all() == receipts
        assert session.execute(text("SELECT * FROM operation_logs")).all() == logs
        new = _upload(session, shelf_id, owner_id, "new-after-upgrade")
        assert new["id"] > old["id"]
        with pytest.raises(NotFound):
            storage_photos.delete_photo(session, old["id"], version=1,
                                        idempotency_key="late-old-delete",
                                        operator_member_id=owner_id)
        assert session.scalar(select(ShelfPhoto.id)) == new["id"]


def test_upgrade_already_fixed_table_preserves_sequence(migration_db):
    cfg, engine = migration_db
    # 模拟已经使用修订版 l3 的库：AUTOINCREMENT 曾使用 700，之后照片被删光。
    with engine.begin() as conn:
        ddl = conn.scalar(text("SELECT sql FROM sqlite_master WHERE name='shelf_photos'"))
        conn.exec_driver_sql("DROP TABLE shelf_photos")
        ddl = ddl.replace("\tid INTEGER NOT NULL,", "\tid INTEGER PRIMARY KEY AUTOINCREMENT,")
        ddl = ddl.replace("CONSTRAINT pk_shelf_photos PRIMARY KEY (id),", "")
        conn.exec_driver_sql(ddl)
        shelf_id = conn.execute(text("INSERT INTO storage_rooms (code,name) VALUES ('r','R')")).lastrowid
        conn.execute(text("INSERT INTO storage_shelves (id,room_id,code,name) VALUES (1,:r,'s','S')"), {"r": shelf_id})
        conn.execute(text("INSERT INTO shelf_photos (id,shelf_id,relative_path,mime_type,width,height) "
                          "VALUES (700,1,'old.jpg','image/jpeg',8,8)"))
        conn.execute(text("DELETE FROM shelf_photos"))
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _PREVIOUS)
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT seq FROM sqlite_sequence WHERE name='shelf_photos'")) >= 700
        result = conn.execute(text("INSERT INTO shelf_photos (shelf_id,relative_path,mime_type,width,height) "
                                   "VALUES (1,'new.jpg','image/jpeg',8,8)"))
        assert result.lastrowid > 700


def test_forward_migration_is_noop_for_postgresql(migration_db, monkeypatch):
    cfg, _ = migration_db
    monkeypatch.setattr(settings, "database_url", "postgresql://unused/unused")
    output = StringIO()
    cfg.output_buffer = output
    command.upgrade(cfg, f"{_PREVIOUS}:head", sql=True)
    sql = output.getvalue()
    assert "sqlite_sequence" not in sql
    assert "CREATE TABLE" not in sql
    assert "DROP TABLE" not in sql
