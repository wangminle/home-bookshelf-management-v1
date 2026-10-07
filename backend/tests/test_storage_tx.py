"""LOC-07 写事务/并发/幂等/审计基础回归。

覆盖 M0 冻结契约（design/checkpoints/实体书架位置管理-M0契约冻结-20261006.md）：
- 幂等：同 key 同摘要重放已存回执、同 key 不同摘要 409 且零写入、
  ≥4 真实线程并发同 key 只提交一次（业务写入与回执各一行）；
- 事务边界：中途失败/审计失败整体回滚（业务、回执、OperationLog 同事务）；
- 版本乐观锁：条件更新命中/失效、副本 placement_version 列；
- 锁序：双向资源顺序不死锁、锁定后按重读版本更新；
- 重试：仅 OperationalError 回滚后重试，上限 3 次，业务冲突不重试。

锁序/幂等的并发验证在 SQLite 上进行；PostgreSQL 分支（SELECT FOR UPDATE）
本机无环境，按 LOC-03 部署验证约定如实记录为未实机验证。
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Book,
    BookCopy,
    Member,
    OperationLog,
    ShelfCell,
    ShelfLayer,
    StorageRoom,
    StorageShelf,
)
from app.models.storage import LocationOperation
from app.services import storage_tx
from app.services.storage_tx import (
    IdempotencyConflict,
    InvalidRelation,
    LocationArchived,
    NotFound,
    PlacementChanged,
)
from app.utils import operation_log
from app.utils.operation_log import log_operation


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _make_member(s, *, username="loc_owner") -> int:
    member = Member(username=username, name="位置店主", role="owner")
    s.add(member)
    s.flush()
    member_id = member.id
    s.commit()
    return member_id


def _make_structure(s, *, room_code="R1", shelf_code="A01") -> tuple[int, int, int]:
    room = StorageRoom(code=room_code, name=f"房间{room_code}")
    s.add(room)
    s.flush()
    shelf = StorageShelf(room_id=room.id, code=shelf_code, name=f"{shelf_code} 书架")
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


# ── 摘要规范化 ──

def test_payload_digest_canonical() -> None:
    d1 = storage_tx.payload_digest({"b": [2, 3], "a": "甲"})
    d2 = storage_tx.payload_digest({"a": "甲", "b": [2, 3]})
    assert d1 == d2
    assert len(d1) == 64
    assert storage_tx.payload_digest({"a": 1}) != storage_tx.payload_digest({"a": 2})
    assert storage_tx.payload_digest(None) == storage_tx.payload_digest({})


# ── 错误类型与 HTTP 映射 ──

def test_location_error_http_mapping() -> None:
    exc = LocationArchived()
    http = exc.as_http_exception()
    assert http.status_code == 409
    assert http.detail == {"code": "LOCATION_ARCHIVED", "message": exc.message}
    assert IdempotencyConflict().status_code == 409
    assert PlacementChanged().status_code == 409
    assert InvalidRelation().status_code == 422
    assert NotFound().status_code == 404


# ── 幂等：登记 / 完成 / 重放 / 冲突 ──

def test_begin_complete_then_replay(db_engine) -> None:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        member_id = _make_member(s)
    with SessionLocal() as s:
        begin = storage_tx.begin_operation(
            s, operator_member_id=member_id, idempotency_key="k1", payload={"a": 1})
        assert not begin.replayed
        storage_tx.complete_operation(s, begin.operation, {"room_id": 7})
        s.commit()

    with SessionLocal() as s:
        replay = storage_tx.begin_operation(
            s, operator_member_id=member_id, idempotency_key="k1", payload={"a": 1})
        assert replay.replayed
        assert replay.result == {"room_id": 7}
        # 不同操作者同 key 互不影响
        other = _make_member(s, username="loc_owner2")
        fresh = storage_tx.begin_operation(
            s, operator_member_id=other, idempotency_key="k1", payload={"a": 1})
        assert not fresh.replayed
        s.rollback()

    with SessionLocal() as s:
        rows = s.scalars(select(LocationOperation).where(
            LocationOperation.idempotency_key == "k1")).all()
        assert len(rows) == 1


def test_digest_conflict_writes_nothing(db_engine) -> None:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        member_id = _make_member(s)
    with SessionLocal() as s:
        begin = storage_tx.begin_operation(
            s, operator_member_id=member_id, idempotency_key="k2", payload={"a": 1})
        storage_tx.complete_operation(s, begin.operation, {"ok": True})
        s.commit()

    with SessionLocal() as s:
        with pytest.raises(IdempotencyConflict) as exc_info:
            storage_tx.begin_operation(
                s, operator_member_id=member_id, idempotency_key="k2", payload={"a": 2})
        assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
        assert exc_info.value.status_code == 409
        s.rollback()

    with SessionLocal() as s:
        assert s.scalars(select(StorageRoom)).all() == []
        rows = s.scalars(select(LocationOperation)).all()
        assert len(rows) == 1


# ── 事务边界：中途失败 / 审计失败整体回滚 ──

def test_mid_failure_rolls_back_everything(db_engine) -> None:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        member_id = _make_member(s)

    with SessionLocal() as s:
        def fn(db):
            begin = storage_tx.begin_operation(
                db, operator_member_id=member_id, idempotency_key="k-fail", payload={"x": 1})
            db.add(StorageRoom(code="RB", name="回滚房间"))
            log_operation(db, action="storage.test", member_id=member_id, payload={"x": 1})
            db.flush()
            raise InvalidRelation("层不属于该书架")

        with pytest.raises(InvalidRelation):
            storage_tx.run_in_txn(s, fn)
        s.rollback()

    with SessionLocal() as s:
        assert s.scalars(select(StorageRoom)).all() == []
        assert s.scalars(select(LocationOperation)).all() == []
        assert s.scalars(select(OperationLog).where(
            OperationLog.action == "storage.test")).all() == []


def test_audit_failure_fails_whole_tx(db_engine, monkeypatch) -> None:
    """审计纪律：log_operation 失败则整个位置事务失败，不留业务写入与回执。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        member_id = _make_member(s)

    def broken_log(db, **kwargs):
        raise RuntimeError("审计存储不可用")

    monkeypatch.setattr(operation_log, "log_operation", broken_log)

    with SessionLocal() as s:
        def fn(db):
            begin = storage_tx.begin_operation(
                db, operator_member_id=member_id, idempotency_key="k-audit", payload={"x": 1})
            db.add(StorageRoom(code="RA", name="审计回滚房间"))
            db.flush()
            operation_log.log_operation(db, action="storage.test", member_id=member_id)
            storage_tx.complete_operation(db, begin.operation, {"ok": True})
            db.commit()

        with pytest.raises(RuntimeError):
            storage_tx.run_in_txn(s, fn)
        s.rollback()

    with SessionLocal() as s:
        assert s.scalars(select(StorageRoom)).all() == []
        assert s.scalars(select(LocationOperation)).all() == []


# ── 版本乐观锁 ──

def test_bump_version_conditional(db_engine) -> None:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        shelf_id, _, _ = _make_structure(s)
        book = Book(title="版本书")
        s.add(book)
        s.flush()
        copy = BookCopy(book_id=book.id)
        s.add(copy)
        s.flush()
        copy_id = copy.id
        s.commit()

    with SessionLocal() as s:
        storage_tx.bump_version(s, StorageShelf, shelf_id, 1)
        s.commit()
        s.expire_all()
        assert s.get(StorageShelf, shelf_id).version == 2
        # 过期版本 → 409 PLACEMENT_CHANGED
        with pytest.raises(PlacementChanged) as exc_info:
            storage_tx.bump_version(s, StorageShelf, shelf_id, 1)
        assert exc_info.value.code == "PLACEMENT_CHANGED"
        s.rollback()
        # 不存在的行同样按版本失效处理（rowcount=0）
        with pytest.raises(PlacementChanged):
            storage_tx.bump_version(s, StorageShelf, 999999, 1)
        s.rollback()
        # 副本定位版本列
        storage_tx.bump_version(s, BookCopy, copy_id, 1, column="placement_version")
        s.commit()
        s.expire_all()
        assert s.get(BookCopy, copy_id).placement_version == 2
        assert s.get(StorageShelf, shelf_id).version == 2


# ── 锁序 ──

def test_lock_resources_missing_raises_not_found(db_engine) -> None:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        shelf_id, _, _ = _make_structure(s)
        with pytest.raises(NotFound):
            storage_tx.lock_resources(s, {"shelf": [shelf_id, 999999]})
        s.rollback()


def test_collect_ancestors_expands_full_chain(db_engine) -> None:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        shelf_id, layer_id, cell_id = _make_structure(s)
        room_id = s.get(StorageShelf, shelf_id).room_id
        book = Book(title="祖先书")
        s.add(book)
        s.flush()
        copy = BookCopy(book_id=book.id, placement_shelf_id=shelf_id, placement_cell_id=cell_id)
        s.add(copy)
        s.flush()
        copy_id = copy.id
        s.commit()

        res = storage_tx.collect_ancestors(s, copy_ids=[copy_id])
        assert res == {
            "room": [room_id], "shelf": [shelf_id], "layer": [layer_id],
            "cell": [cell_id], "copy": [copy_id],
        }


def test_bidirectional_lock_order_no_deadlock(db_engine) -> None:
    """两线程按相反业务顺序锁定同一对资源：锁序固定为类型序 + ID 升序，
    双方实际加锁顺序一致，不死锁；锁定后重读版本再更新，双双提交。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        _, _, cell_a = _make_structure(s, room_code="R1", shelf_code="A01")
        _, _, cell_b = _make_structure(s, room_code="R2", shelf_code="B01")

    errors: list[BaseException] = []

    def worker(ids: list[int]) -> None:
        try:
            with SessionLocal() as s:
                def fn(db):
                    resources = storage_tx.collect_ancestors(db, cell_ids=ids)
                    storage_tx.lock_resources(db, resources)
                    for cid in ids:
                        current = db.get(ShelfCell, cid).version
                        storage_tx.bump_version(db, ShelfCell, cid, current)
                    db.commit()
                storage_tx.run_in_txn(s, fn)
        except BaseException as exc:  # noqa: BLE001 - 汇总线程错误
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=([cell_a, cell_b],))
    t2 = threading.Thread(target=worker, args=([cell_b, cell_a],))
    t1.start()
    t2.start()
    t1.join(30)
    t2.join(30)
    assert not t1.is_alive() and not t2.is_alive(), "疑似死锁：线程未在限时内结束"
    assert not errors, errors
    with SessionLocal() as s:
        assert s.get(ShelfCell, cell_a).version == 3
        assert s.get(ShelfCell, cell_b).version == 3


# ── 并发幂等：≥4 真实线程同 key 只提交一次 ──

def test_concurrent_same_key_commits_once(db_engine) -> None:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        member_id = _make_member(s)

    results: list[dict] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with SessionLocal() as s:
                def fn(db):
                    begin = storage_tx.begin_operation(
                        db, operator_member_id=member_id,
                        idempotency_key="k-conc", payload={"room": "RC"})
                    if begin.replayed:
                        return begin.result
                    room = StorageRoom(code="RC", name="并发房间")
                    db.add(room)
                    db.flush()
                    receipt = {"room_id": room.id}
                    storage_tx.complete_operation(db, begin.operation, receipt)
                    db.commit()
                    return receipt
                outcome = storage_tx.run_in_txn(s, fn)
                with lock:
                    results.append(outcome)
        except BaseException as exc:  # noqa: BLE001 - 汇总线程错误
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "并发线程未在限时内结束"
    assert not errors, errors
    assert len(results) == 4
    assert all(r == results[0] for r in results), results

    with SessionLocal() as s:
        rooms = s.scalars(select(StorageRoom).where(StorageRoom.code == "RC")).all()
        assert len(rooms) == 1
        ops = s.scalars(select(LocationOperation).where(
            LocationOperation.idempotency_key == "k-conc")).all()
        assert len(ops) == 1
        assert ops[0].result_json is not None


# ── 重试策略 ──

def test_retry_only_on_operational_error_with_limit(db_engine) -> None:
    SessionLocal = _session(db_engine)
    calls = 0

    def always_busy(db):
        nonlocal calls
        calls += 1
        raise OperationalError("UPDATE t", {}, Exception("database is locked"))

    with SessionLocal() as s:
        with pytest.raises(OperationalError):
            storage_tx.run_in_txn(s, always_busy)
    assert calls == 1 + 3  # 首次 + 3 次重试

    conflict_calls = 0

    def business_conflict(db):
        nonlocal conflict_calls
        conflict_calls += 1
        raise PlacementChanged()

    with SessionLocal() as s:
        with pytest.raises(PlacementChanged):
            storage_tx.run_in_txn(s, business_conflict)
    assert conflict_calls == 1  # 业务冲突不重试
