"""LOC-04 位置实体与迁移回归：七张位置表 + BookCopy placement 字段的约束可验证。

覆盖设计 §6 冻结契约：层格同属一个书架（复合 FK）、架内 code 唯一、
cell 必带 shelf、仅实体副本可定位、每架最多一张主图、幂等 key 同操作者唯一、
旧副本默认未结构化定位。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Book,
    BookCopy,
    LocationOperation,
    Member,
    ShelfCell,
    ShelfLayer,
    ShelfPhoto,
    StorageRoom,
    StorageShelf,
)


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _make_structure(s, *, room_code="R1", shelf_code="A01") -> tuple[int, int, int]:
    """建 房间→书架→一层一格 并提交，返回 (shelf_id, layer_id, cell_id)。"""
    room = StorageRoom(code=room_code, name=f"房间{room_code}")
    s.add(room)
    s.flush()
    shelf = StorageShelf(room_id=room.id, code=shelf_code, name=f"{shelf_code} 书架", position_note="靠窗右侧")
    s.add(shelf)
    s.flush()
    layer = ShelfLayer(shelf_id=shelf.id, label="第 1 层", sort_order=0)
    s.add(layer)
    s.flush()
    cell = ShelfCell(shelf_id=shelf.id, layer_id=layer.id, code="L", label="左格", sort_order=0)
    s.add(cell)
    s.flush()
    ids = (shelf.id, layer.id, cell.id)
    s.commit()
    return ids


def _make_copy(s, *, copy_type="physical") -> int:
    book = Book(title="书甲")
    s.add(book)
    s.flush()
    copy = BookCopy(book_id=book.id, copy_type=copy_type)
    s.add(copy)
    s.flush()
    copy_id = copy.id
    s.commit()
    return copy_id


def test_migration_creates_storage_tables(db_engine):
    """迁移后七张位置表与 book_copies placement 列存在。"""
    with db_engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))}
        for t in ("storage_rooms", "storage_shelves", "shelf_layers", "shelf_cells",
                  "shelf_photos", "location_operations", "location_file_gc_jobs"):
            assert t in tables, t
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(book_copies)"))}
        assert {"placement_shelf_id", "placement_cell_id", "placement_version"} <= cols


def test_room_code_and_shelf_code_unique(db_engine):
    SessionLocal = _session(db_engine)
    shelf_id, _, _ = None, None, None
    with SessionLocal() as s:
        shelf_id, _, _ = _make_structure(s)
        s.add(StorageRoom(code="R1", name="重复房间"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        room_id = s.scalar(select(StorageRoom.id).where(StorageRoom.code == "R1"))
        s.add(StorageShelf(room_id=room_id, code="A01", name="重复书架"))
        with pytest.raises(IntegrityError):
            s.flush()


def test_cell_layer_must_belong_to_same_shelf(db_engine):
    """复合 FK：cell 的 layer 必须属于同一个 shelf。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        _, layer_a_id, _ = _make_structure(s)
        shelf_b_id, _, _ = _make_structure(s, room_code="R2", shelf_code="B01")
        # B 架的格子挂上 A 架的层 → 复合 FK 拒绝
        s.add(ShelfCell(shelf_id=shelf_b_id, layer_id=layer_a_id, code="X", label="跨架", sort_order=1))
        with pytest.raises(IntegrityError):
            s.flush()


def test_cell_code_unique_within_shelf(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        shelf_id, layer_id, _ = _make_structure(s)
        s.add(ShelfCell(shelf_id=shelf_id, layer_id=layer_id, code="L", label="重复", sort_order=1))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # 不同书架允许同 code（_make_structure 在 B 架同样建了 code='L' 的格子）
        shelf_b_id, _, _ = _make_structure(s, room_code="R2", shelf_code="B01")
        codes = s.scalars(select(ShelfCell.code).where(ShelfCell.shelf_id == shelf_b_id)).all()
        assert codes == ["L"]


def test_placement_requires_shelf_and_physical_copy(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        shelf_id, _, cell_id = _make_structure(s)
        copy_id = _make_copy(s)
        # cell 非空但无 shelf → CHECK 拒绝
        copy = s.get(BookCopy, copy_id)
        copy.placement_cell_id = cell_id
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # 电子副本定位 → CHECK 拒绝
        digital_id = _make_copy(s, copy_type="digital")
        digital = s.get(BookCopy, digital_id)
        digital.placement_shelf_id = shelf_id
        with pytest.raises(IntegrityError):
            s.flush()


def test_placement_cell_must_belong_to_its_shelf(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        shelf_a_id, _, cell_id = _make_structure(s)
        shelf_b_id, _, _ = _make_structure(s, room_code="R2", shelf_code="B01")
        copy_id = _make_copy(s)
        copy = s.get(BookCopy, copy_id)
        copy.placement_shelf_id = shelf_b_id
        copy.placement_cell_id = cell_id  # cell 属于 A 架 → 复合 FK 拒绝
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        copy = s.get(BookCopy, copy_id)
        copy.placement_shelf_id = shelf_a_id
        copy.placement_cell_id = cell_id
        s.commit()
        s.expire_all()
        kept = s.get(BookCopy, copy_id)
        assert kept.placement_shelf_id == shelf_a_id
        assert kept.placement_cell_id == cell_id
        assert kept.placement_version == 1


def test_existing_copy_defaults_unplaced(db_engine):
    """旧副本默认未结构化定位：placement 字段为空、版本 1。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        copy_id = _make_copy(s)
        kept = s.get(BookCopy, copy_id)
        assert kept.placement_shelf_id is None
        assert kept.placement_cell_id is None
        assert kept.placement_version == 1


def test_primary_photo_unique_per_shelf(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        shelf_id, _, _ = _make_structure(s)
        shelf_b_id, _, _ = _make_structure(s, room_code="R2", shelf_code="B01")
        s.add(ShelfPhoto(shelf_id=shelf_id, relative_path="shelf_photos/1/a.jpg",
                         mime_type="image/jpeg", width=100, height=100, is_primary=True))
        s.commit()
        # 同架第二张主图 → 部分唯一索引拒绝
        s.add(ShelfPhoto(shelf_id=shelf_id, relative_path="shelf_photos/1/b.jpg",
                         mime_type="image/jpeg", width=100, height=100, is_primary=True))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # 他架主图与本架非主图不受限
        s.add(ShelfPhoto(shelf_id=shelf_b_id, relative_path="shelf_photos/2/c.jpg",
                         mime_type="image/jpeg", width=100, height=100, is_primary=True))
        s.add(ShelfPhoto(shelf_id=shelf_id, relative_path="shelf_photos/1/d.jpg",
                         mime_type="image/jpeg", width=100, height=100, is_primary=False))
        s.commit()


def test_location_operation_key_unique_per_operator(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        member = Member(username="owner1", name="店主一", role="owner")
        s.add(member)
        s.flush()
        member_id = member.id
        s.add(LocationOperation(operator_member_id=member_id, idempotency_key="k1", payload_digest="d1"))
        s.commit()
        # 同操作者同 key（即使摘要不同）→ 唯一约束拒绝（摘要冲突在领域层判 409）
        s.add(LocationOperation(operator_member_id=member_id, idempotency_key="k1", payload_digest="d2"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # 不同操作者允许同 key
        other = Member(username="owner2", name="店主二", role="owner")
        s.add(other)
        s.flush()
        s.add(LocationOperation(operator_member_id=other.id, idempotency_key="k1", payload_digest="d1"))
        s.commit()


def test_structure_relationships_navigable(db_engine):
    """ORM 关系可用：room → shelves → layers/cells/photos。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        _make_structure(s)
        room = s.scalar(select(StorageRoom))
        assert room.shelves[0].code == "A01"
        assert room.shelves[0].layers[0].label == "第 1 层"
        assert room.shelves[0].cells[0].code == "L"
        assert room.shelves[0].cells[0].layer.label == "第 1 层"
