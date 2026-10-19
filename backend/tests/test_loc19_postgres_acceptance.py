"""LOC-19：位置模块在 PostgreSQL 上的并发与约束验收（设计 §12 并发/故障行）。

仅在设置 TEST_DATABASE_URL（PostgreSQL 管理库 URL）时运行，否则整体跳过——
**跳过不计为通过**：验收记录中 PG 行必须引用一次真实的带 TEST_DATABASE_URL 的运行结果。

运行方式（需 psycopg2 驱动与可建库的 PG 账号）::

    TEST_DATABASE_URL="postgresql+psycopg2://user:pw@127.0.0.1:5432/postgres" \\
        python -m pytest backend/tests/test_loc19_postgres_acceptance.py -q

本文件直接走服务层/数据库（不经 Web 会话）：Web 会话与 Agent 令牌的比较逻辑
按 SQLite 的 naive 时间设计，PG 兼容属全站认证层问题（见验收记录「发现」），
不属于位置模块范围。
"""
from __future__ import annotations

import os
import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db as db_module
from app.models import Book, BookCopy, Member, StorageShelf
from app.models.storage import ShelfPhoto, StorageRoom
from app.schemas.storage import (
    LayerInput,
    CellInput,
    PlacementTarget,
    PlacementUpdate,
    RoomCreate,
    ShelfCreate,
    ShelfUpdate,
)
from app.services import storage_locations, storage_placement
from app.services.storage_tx import LocationInUse, PlacementChanged

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").strip(),
    reason="LOC-19：需设置 TEST_DATABASE_URL 指向 PostgreSQL 才运行（跳过不计为验收通过）",
)


def _owner(db: Session) -> int:
    member = Member(name="PG Owner", role="owner")
    db.add(member)
    db.commit()
    return member.id


def _world(db: Session, owner: int, tag: str) -> dict:
    room = storage_locations.create_room(
        db, RoomCreate(idempotency_key=f"r-{tag}", code=f"R-{tag}", name=f"房间{tag}"),
        operator_member_id=owner)["room"]
    shelves = []
    for suffix in ("A", "B"):
        shelves.append(storage_locations.create_shelf(
            db,
            ShelfCreate(
                idempotency_key=f"s-{tag}-{suffix}", room_id=room["id"],
                code=f"{tag}{suffix}", name=f"书架{tag}{suffix}",
                layers=[LayerInput(label="上层", cells=[CellInput(label="左"), CellInput(label="右")])]),
            operator_member_id=owner)["shelf"])
    a, b = shelves
    return {"room": room, "a": a, "b": b,
            "cell_a": a["layers"][0]["cells"][0], "cell_b": b["layers"][0]["cells"][0]}


def _copy(db: Session, title: str) -> BookCopy:
    book = Book(title=title)
    db.add(book)
    db.flush()
    copy = BookCopy(book_id=book.id, copy_type="physical", status="in_shelf")
    db.add(copy)
    db.commit()
    return copy


def _run_threads(workers: list) -> None:
    threads = [threading.Thread(target=w) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "线程超时：疑似死锁"


# ── 约束 ──


def test_pg_dialect_and_partial_unique_index(db_session: Session) -> None:
    assert db_session.bind.dialect.name == "postgresql"
    indexdef = db_session.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_shelf_photos_primary'")).scalar_one()
    assert "UNIQUE" in indexdef and "is_primary" in indexdef and "WHERE" in indexdef


def test_pg_one_primary_photo_per_shelf(db_session: Session) -> None:
    owner = _owner(db_session)
    w = _world(db_session, owner, "P1")

    def photo(primary: bool) -> ShelfPhoto:
        return ShelfPhoto(shelf_id=w["a"]["id"], relative_path=f"p/{primary}-{id(object())}.jpg",
                          mime_type="image/jpeg", width=10, height=10, is_primary=primary)

    db_session.add_all([photo(True), photo(False), photo(False)])
    db_session.commit()  # 一主图 + 多非主图合法
    db_session.add(photo(True))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_pg_composite_fk_rejects_cell_of_other_shelf(db_session: Session) -> None:
    owner = _owner(db_session)
    w = _world(db_session, owner, "P2")
    copy = _copy(db_session, "跨架格")
    copy.placement_shelf_id = w["a"]["id"]
    copy.placement_cell_id = w["cell_b"]["id"]  # 格子属于另一书架
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ── 并发 ──


def test_pg_move_vs_archive_never_leaves_archived_reference(db_session: Session) -> None:
    owner = _owner(db_session)
    outcomes: list[str] = []
    for round_no in range(8):
        w = _world(db_session, owner, f"M{round_no}")
        copy = _copy(db_session, f"并发书{round_no}")
        storage_placement.set_placement(
            db_session, copy.book_id, copy.id,
            PlacementUpdate(placement_version=1, target=PlacementTarget(
                shelf_id=w["a"]["id"], cell_id=w["cell_a"]["id"])),
            operator_member_id=owner)
        copy_id, book_id = copy.id, copy.book_id
        errors: list[object] = []
        barrier = threading.Barrier(2)

        def move() -> None:
            s = db_module.SessionLocal()
            try:
                barrier.wait(10)
                for _ in range(30):
                    ver = s.get(BookCopy, copy_id).placement_version
                    try:
                        storage_placement.set_placement(
                            s, book_id, copy_id,
                            PlacementUpdate(placement_version=ver, target=PlacementTarget(
                                shelf_id=w["b"]["id"], cell_id=w["cell_b"]["id"])),
                            operator_member_id=owner)
                        return
                    except PlacementChanged:
                        s.rollback()
                errors.append("move retry exhausted")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                s.close()

        def archive() -> None:
            s = db_module.SessionLocal()
            try:
                barrier.wait(10)
                for _ in range(30):
                    ver = s.get(StorageShelf, w["a"]["id"]).version
                    try:
                        storage_locations.update_shelf(
                            s, w["a"]["id"], ShelfUpdate(version=ver, archived=True),
                            operator_member_id=owner)
                        return
                    except PlacementChanged:
                        s.rollback()
                    except LocationInUse:
                        s.rollback()
                        return
                errors.append("archive retry exhausted")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                s.close()

        _run_threads([move, archive])
        assert not errors, errors
        db_session.expire_all()
        shelf_a = db_session.get(StorageShelf, w["a"]["id"])
        final = db_session.get(BookCopy, copy_id)
        assert final.placement_shelf_id == w["b"]["id"]  # 移动在重试后必成功
        outcomes.append("archived" if shelf_a.archived_at else "in_use")
        # 不变量：没有任何副本引用已归档书架
        bad = db_session.scalars(
            select(BookCopy.id)
            .join(StorageShelf, StorageShelf.id == BookCopy.placement_shelf_id)
            .where(StorageShelf.archived_at.is_not(None))).all()
        assert bad == []
    assert len(outcomes) == 8


def test_pg_same_version_concurrent_move_has_single_winner(db_session: Session) -> None:
    owner = _owner(db_session)
    w = _world(db_session, owner, "V1")
    copy = _copy(db_session, "同版本并发")
    copy_id, book_id = copy.id, copy.book_id
    barrier = threading.Barrier(4)
    results: list[str] = []
    lock = threading.Lock()

    def mover() -> None:
        s = db_module.SessionLocal()
        try:
            barrier.wait(10)
            try:
                storage_placement.set_placement(
                    s, book_id, copy_id,
                    PlacementUpdate(placement_version=1, target=PlacementTarget(
                        shelf_id=w["b"]["id"], cell_id=w["cell_b"]["id"])),
                    operator_member_id=owner)
                tag = "ok"
            except PlacementChanged:
                s.rollback()
                tag = "changed"
            with lock:
                results.append(tag)
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(f"error:{exc!r}")
        finally:
            s.close()

    _run_threads([mover] * 4)
    assert sorted(results) == ["changed", "changed", "changed", "ok"], results
    db_session.expire_all()
    assert db_session.get(BookCopy, copy_id).placement_version == 2


def test_pg_same_idempotency_key_runs_once(db_session: Session) -> None:
    owner = _owner(db_session)
    barrier = threading.Barrier(4)
    ids: list[object] = []
    lock = threading.Lock()

    def creator() -> None:
        s = db_module.SessionLocal()
        try:
            barrier.wait(10)
            res = storage_locations.create_room(
                s, RoomCreate(idempotency_key="same-key", code="IDEMP", name="幂等房间"),
                operator_member_id=owner)
            with lock:
                ids.append(res["room"]["id"])
        except Exception as exc:  # noqa: BLE001
            with lock:
                ids.append(f"error:{exc!r}")
        finally:
            s.close()

    _run_threads([creator] * 4)
    assert len({i for i in ids}) == 1 and isinstance(ids[0], int), ids
    db_session.expire_all()
    assert len(db_session.scalars(select(StorageRoom).where(StorageRoom.code == "IDEMP")).all()) == 1


def test_pg_move_blocks_on_target_shelf_row_lock(db_session: Session) -> None:
    """证明 lock_resources 的 PG 分支确实取了 SELECT … FOR UPDATE 行锁：
    外部事务持有目标书架行的 FOR KEY SHARE（只与 FOR UPDATE 冲突，不挡普通
    非键 UPDATE）时，移动必须阻塞，释放后才完成（并发不变量测试另有乐观版本
    兜底，单靠它们无法区分「有无 FOR UPDATE 行锁」）。"""
    import time

    owner = _owner(db_session)
    w = _world(db_session, owner, "LK")
    copy = _copy(db_session, "行锁书")
    copy_id, book_id = copy.id, copy.book_id

    holder = db_module.SessionLocal()
    holder.execute(text("SELECT id FROM storage_shelves WHERE id = :i FOR KEY SHARE"),
                   {"i": w["b"]["id"]})
    done = threading.Event()
    errors: list[object] = []

    def mover() -> None:
        s = db_module.SessionLocal()
        try:
            storage_placement.set_placement(
                s, book_id, copy_id,
                PlacementUpdate(placement_version=1, target=PlacementTarget(
                    shelf_id=w["b"]["id"], cell_id=w["cell_b"]["id"])),
                operator_member_id=owner)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()
            done.set()

    t = threading.Thread(target=mover)
    t.start()
    try:
        time.sleep(1.5)
        assert not done.is_set(), "目标书架行被 FOR KEY SHARE 持有，移动却没有阻塞：未取 FOR UPDATE 行锁"
    finally:
        holder.rollback()
        holder.close()
    t.join(timeout=30)
    assert done.is_set() and not errors, errors
    db_session.expire_all()
    assert db_session.get(BookCopy, copy_id).placement_shelf_id == w["b"]["id"]
