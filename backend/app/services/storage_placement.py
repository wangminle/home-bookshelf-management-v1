"""LOC-13：单册位置与批量移动领域服务（设计 §7.2/§7.3/§9，M0 契约修订 LOC-13）。

纪律：
- 所有写复用 storage_tx 原语：begin_operation/complete_operation（幂等回执）、
  collect_ancestors（源与目标完整祖先集合，含副本自身）+ lock_resources
  （固定锁序 room → shelf → layer → cell → copy）、bump_version
  （副本用 placement_version 列）、run_in_txn（仅忙/死锁重试）。
- 预览（preview_move）dry-run 不落库：不加锁、不改数据、不登记幂等键，
  返回每册源→目标与将发生的版本变化及 preview_digest。
- 执行（execute_move）原子：重验预览摘要 + 目标结构版本 + 每册
  placement_version，任一失效整批拒绝零写入；移动不新增副本、不改副本状态。
- 清除位置必须显式 keep_legacy_location（schema 层强制）；旧客户端误写
  location 文字一律 409 LEGACY_LOCATION_WRITE。
- 源位置归档不阻断移出（归档时引用检查已阻断新引用，移出属历史清理）；
  目标链任一节点归档一律 409 LOCATION_ARCHIVED。
- 审计只调 log_operation（同事务），payload 记录原/新位置 ID 与展示路径快照
  （§7.3：历史审计保留当时快照，不能只存可变名的外键引用）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Book, BookCopy, ShelfCell, ShelfLayer, StorageRoom, StorageShelf
from app.schemas.storage import (
    PlacementExecuteIn,
    PlacementPreviewIn,
    PlacementUpdate,
)
from app.services import storage_queries, storage_tx
from app.services.storage_tx import (
    InvalidRelation,
    LocationArchived,
    LocationError,
    NotFound,
    PlacementChanged,
)
from app.utils.operation_log import log_operation
from app.utils.serializers import copy_to_out

# LOC-01 冻结：批量移动/补录每批 ≤100 册，超出拒绝
BATCH_MAX_COPIES = 100


class InvalidBatch(LocationError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_BATCH", message, 422)


class InvalidCopyType(LocationError):
    def __init__(self, message: str = "仅实体副本可登记实体位置") -> None:
        super().__init__("INVALID_COPY_TYPE", message, 422)


class LegacyLocationWrite(LocationError):
    def __init__(self, message: str) -> None:
        super().__init__("LEGACY_LOCATION_WRITE", message, 409)


def _validate_batch_shape(copy_ids: list[int]) -> None:
    if not copy_ids:
        raise InvalidBatch("批次不能为空")
    if len(copy_ids) > BATCH_MAX_COPIES:
        raise InvalidBatch(f"每批最多 {BATCH_MAX_COPIES} 册，本批 {len(copy_ids)} 册")
    if len(set(copy_ids)) != len(copy_ids):
        raise InvalidBatch("批次内 copy_id 重复")


def _load_and_validate_target(
    db: Session, shelf_id: int, cell_id: int | None
) -> tuple[StorageShelf, ShelfCell | None]:
    """校验目标位置存在、层格关系合法、目标链无归档（调用方负责先加锁）。"""
    shelf = db.get(StorageShelf, shelf_id)
    if shelf is None:
        raise NotFound(f"书架 {shelf_id} 不存在")
    room = db.get(StorageRoom, shelf.room_id)
    if room is not None and room.archived_at is not None:
        raise LocationArchived(f"目标书架 {shelf_id} 的上级房间已归档")
    if shelf.archived_at is not None:
        raise LocationArchived(f"目标书架 {shelf_id} 已归档")
    cell = None
    if cell_id is not None:
        cell = db.get(ShelfCell, cell_id)
        if cell is None:
            raise NotFound(f"格子 {cell_id} 不存在")
        if cell.shelf_id != shelf_id:
            raise InvalidRelation(f"格子 {cell_id} 不属于书架 {shelf_id}")
        layer = db.get(ShelfLayer, cell.layer_id)
        if layer is not None and layer.archived_at is not None:
            raise LocationArchived(f"目标格子所在层 {cell.layer_id} 已归档")
        if cell.archived_at is not None:
            raise LocationArchived(f"目标格子 {cell_id} 已归档")
    return shelf, cell


def _display(db: Session, shelf_id: int | None, cell_id: int | None,
             cache: dict) -> str | None:
    if shelf_id is None:
        return None
    return storage_queries.build_location_display(db, shelf_id, cell_id, cache=cache)


def _ensure_physical(copy: BookCopy) -> None:
    if copy.copy_type != "physical":
        raise InvalidCopyType(f"副本 {copy.id} 是电子副本，不能登记实体位置")


def _intent_payload(
    *,
    shelf_id: int,
    cell_id: int | None,
    shelf_version: int,
    cell_version: int | None,
    copy_refs: list[tuple[int, int]],
) -> dict:
    """预览/执行共用的规范化意图载荷：preview_digest 对其取摘要（契约修订 LOC-13）。"""
    return {
        "target": {"shelf_id": shelf_id, "cell_id": cell_id,
                   "shelf_version": shelf_version, "cell_version": cell_version},
        "copies": [{"copy_id": cid, "placement_version": ver}
                   for cid, ver in sorted(copy_refs)],
    }


# ── 单册分配／移动／清除 ──

def set_placement(
    db: Session,
    book_id: int,
    copy_id: int,
    payload: PlacementUpdate,
    *,
    operator_member_id: int,
) -> dict:
    clearing = payload.target is None
    digest_payload = {
        "op": "storage.placement.clear" if clearing else "storage.placement.set",
        "book_id": book_id,
        "copy_id": copy_id,
        "target": payload.target.model_dump() if payload.target else None,
        "keep_legacy_location": payload.keep_legacy_location,
        "location": payload.location,
    }

    def fn(db: Session) -> dict:
        begin = None
        if payload.idempotency_key:
            begin = storage_tx.begin_operation(
                db, operator_member_id=operator_member_id,
                idempotency_key=payload.idempotency_key, payload=digest_payload)
            if begin.replayed:
                return begin.result
        resources = storage_tx.collect_ancestors(
            db,
            shelf_ids=[payload.target.shelf_id] if payload.target else [],
            cell_ids=[payload.target.cell_id]
            if payload.target and payload.target.cell_id is not None else [],
            copy_ids=[copy_id])
        storage_tx.lock_resources(db, resources)

        copy = db.get(BookCopy, copy_id)
        # 锁后校验：copy 必须属于路径中的 book_id（§9：不接受改路径操作他书副本）
        if copy.book_id != book_id:
            raise NotFound(f"副本 {copy_id} 不存在")
        if payload.location is not None:
            # 契约冻结：旧客户端向已结构化定位副本写 location → 409；
            # 本端点一律不接受 location 文字字段（契约修订 LOC-13）
            if copy.placement_shelf_id is not None:
                raise LegacyLocationWrite(
                    "副本已结构化定位，请使用位置接口而非旧 location 文字")
            raise LegacyLocationWrite(
                "placement 端点不接受 location 文字字段；旧文字仅可在创建副本时登记")
        _ensure_physical(copy)

        shelf = cell = None
        if not clearing:
            shelf, cell = _load_and_validate_target(
                db, payload.target.shelf_id, payload.target.cell_id)

        cache: dict = {}
        from_shelf, from_cell = copy.placement_shelf_id, copy.placement_cell_id
        from_display = _display(db, from_shelf, from_cell, cache)
        storage_tx.bump_version(db, BookCopy, copy_id, payload.placement_version,
                                column="placement_version")
        if clearing:
            copy.placement_shelf_id = None
            copy.placement_cell_id = None
            if payload.keep_legacy_location is False:
                copy.location = None
            action = "storage.placement.clear"
        else:
            copy.placement_shelf_id = shelf.id
            copy.placement_cell_id = cell.id if cell else None
            action = "storage.placement.set"
        db.flush()
        db.refresh(copy)
        to_display = _display(db, copy.placement_shelf_id, copy.placement_cell_id, cache)
        log_operation(db, action=action, member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id if begin else None,
                               "copy_id": copy_id, "book_id": book_id,
                               "from": {"shelf_id": from_shelf, "cell_id": from_cell,
                                        "display": from_display},
                               "to": {"shelf_id": copy.placement_shelf_id,
                                      "cell_id": copy.placement_cell_id,
                                      "display": to_display},
                               "from_version": payload.placement_version,
                               "to_version": copy.placement_version,
                               "keep_legacy_location": payload.keep_legacy_location
                               if clearing else None})
        result = {"copy": copy_to_out(copy, include_placement=True, db=db)
                  .model_dump(mode="json")}
        if begin is not None:
            storage_tx.complete_operation(db, begin.operation, result)
        db.commit()
        return result

    return storage_tx.run_in_txn(db, fn)


# ── 批量移动：预览（dry-run 不落库） ──

def preview_move(db: Session, payload: PlacementPreviewIn) -> dict:
    copy_ids = [c.copy_id for c in payload.copies]
    _validate_batch_shape(copy_ids)
    shelf, cell = _load_and_validate_target(db, payload.target.shelf_id,
                                            payload.target.cell_id)
    cache: dict = {}
    to_display = _display(db, shelf.id, cell.id if cell else None, cache)
    items: list[dict] = []
    refs: list[tuple[int, int]] = []
    for copy_id in sorted(copy_ids):
        copy = db.get(BookCopy, copy_id)
        if copy is None:
            raise NotFound(f"副本 {copy_id} 不存在")
        _ensure_physical(copy)
        book = db.get(Book, copy.book_id)
        items.append({
            "copy_id": copy.id,
            "book_id": copy.book_id,
            "book_title": book.title if book else None,
            "from": {"shelf_id": copy.placement_shelf_id,
                     "cell_id": copy.placement_cell_id,
                     "display": _display(db, copy.placement_shelf_id,
                                         copy.placement_cell_id, cache)},
            "to": {"shelf_id": shelf.id, "cell_id": cell.id if cell else None,
                   "display": to_display},
            "placement_version": copy.placement_version,
            "new_placement_version": copy.placement_version + 1,
        })
        refs.append((copy.id, copy.placement_version))
    intent = _intent_payload(
        shelf_id=shelf.id, cell_id=cell.id if cell else None,
        shelf_version=shelf.version,
        cell_version=cell.version if cell else None,
        copy_refs=refs)
    return {
        "target": {"shelf_id": shelf.id, "cell_id": cell.id if cell else None,
                   "shelf_version": shelf.version,
                   "cell_version": cell.version if cell else None,
                   "display": to_display},
        "copies": items,
        "preview_digest": storage_tx.payload_digest(intent),
    }


# ── 批量移动：原子执行 ──

def execute_move(
    db: Session,
    payload: PlacementExecuteIn,
    *,
    operator_member_id: int,
) -> dict:
    copy_ids = [c.copy_id for c in payload.copies]
    _validate_batch_shape(copy_ids)
    intent = _intent_payload(
        shelf_id=payload.target.shelf_id, cell_id=payload.target.cell_id,
        shelf_version=payload.target.shelf_version,
        cell_version=payload.target.cell_version,
        copy_refs=[(c.copy_id, c.placement_version) for c in payload.copies])
    digest_payload = {"op": "storage.placement.move", "intent": intent}

    def fn(db: Session) -> dict:
        begin = storage_tx.begin_operation(
            db, operator_member_id=operator_member_id,
            idempotency_key=payload.idempotency_key, payload=digest_payload)
        if begin.replayed:
            return begin.result
        # 重验预览摘要（绑定目标 + 目标版本 + 副本版本清单）
        if storage_tx.payload_digest(intent) != payload.preview_digest:
            raise PlacementChanged("预览摘要与执行请求不一致，请刷新预览后重试")
        resources = storage_tx.collect_ancestors(
            db, shelf_ids=[payload.target.shelf_id],
            cell_ids=[payload.target.cell_id]
            if payload.target.cell_id is not None else [],
            copy_ids=copy_ids)
        storage_tx.lock_resources(db, resources)

        # 锁后重验目标有效性（§7.3：不符即整体回滚，不持锁补锁）
        shelf, cell = _load_and_validate_target(db, payload.target.shelf_id,
                                                payload.target.cell_id)
        if shelf.version != payload.target.shelf_version:
            raise PlacementChanged(
                f"目标书架 {shelf.id} 期望版本 {payload.target.shelf_version} 已失效")
        if cell is not None and cell.version != payload.target.cell_version:
            raise PlacementChanged(
                f"目标格子 {cell.id} 期望版本 {payload.target.cell_version} 已失效")

        cache: dict = {}
        to_display = _display(db, shelf.id, cell.id if cell else None, cache)
        moved: list[dict] = []
        for ref in sorted(payload.copies, key=lambda c: c.copy_id):
            copy = db.get(BookCopy, ref.copy_id)  # 存在性已由锁阶段保证
            _ensure_physical(copy)
            book = db.get(Book, copy.book_id)
            from_shelf, from_cell = copy.placement_shelf_id, copy.placement_cell_id
            from_display = _display(db, from_shelf, from_cell, cache)
            storage_tx.bump_version(db, BookCopy, copy.id, ref.placement_version,
                                    column="placement_version")
            copy.placement_shelf_id = shelf.id
            copy.placement_cell_id = cell.id if cell else None
            db.flush()
            db.refresh(copy)
            moved.append({
                "copy_id": copy.id, "book_id": copy.book_id,
                "book_title": book.title if book else None,
                "from": {"shelf_id": from_shelf, "cell_id": from_cell,
                         "display": from_display},
                "to": {"shelf_id": shelf.id, "cell_id": cell.id if cell else None,
                       "display": to_display},
                "from_version": ref.placement_version,
                "to_version": copy.placement_version,
            })
        log_operation(db, action="storage.placement.move",
                      member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id,
                               "target": intent["target"], "copies": moved})
        result = {
            "moved": len(moved),
            "target": {**intent["target"], "display": to_display},
            "copies": [{"copy_id": m["copy_id"], "book_id": m["book_id"],
                        "placement_version": m["to_version"]} for m in moved],
        }
        storage_tx.complete_operation(db, begin.operation, result)
        db.commit()
        return result

    return storage_tx.run_in_txn(db, fn)
