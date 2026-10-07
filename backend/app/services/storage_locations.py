"""LOC-06：房间／书架／层格领域服务。

契约依据：
- design/plans/实体书架与图书位置管理-功能分析和设计-20261005.md §6/§7.1/§9；
- design/checkpoints/实体书架位置管理-M0契约冻结-20261006.md（LOC-02/LOC-03）。

纪律：
- 所有写路径复用 storage_tx 原语——begin_operation/complete_operation（幂等
  回执）、collect_ancestors/lock_resources（固定锁序）、bump_version（版本
  乐观锁）、run_in_txn（仅忙/死锁重试）；事务边界由本模块 fn 内显式 commit。
- 审计只调 log_operation（add 不 commit），与业务修改、幂等回执同事务；
  审计失败则整体回滚。不使用 log_and_commit。
- 稳定 ID：改名/排序只更新标签字段；归档是状态位（archived_at），不做物理
  删除；合并/拆分通过「归档旧格 + 新建格子」表达，有副本引用一律 409。
- 归档语义（§7.1）：归档 room/shelf/layer 前检查全部后代与所有副本引用
  （含 lent_out/lost 等非在架状态）；归档祖先不级联改后代，但后代的读写
  一律先检查祖先；恢复子对象前需先恢复祖先。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    BookCopy,
    ShelfCell,
    ShelfLayer,
    StorageRoom,
    StorageShelf,
)
from app.models.storage import LocationOperation
from app.schemas.storage import (
    CellOut,
    LayerDetailOut,
    LayerInput,
    LayoutPut,
    RoomCreate,
    RoomOut,
    RoomUpdate,
    ShelfCreate,
    ShelfDetailOut,
    ShelfOut,
    ShelfUpdate,
)
from app.services import storage_tx
from app.services.storage_tx import (
    LocationArchived,
    LocationError,
    LocationInUse,
    NotFound,
    InvalidRelation,
)
from app.utils.operation_log import log_operation

# 编号冲突：冻结错误码表未覆盖 code 唯一性冲突，扩展 CODE_CONFLICT（409）并记录
_CODE_CONFLICT = "CODE_CONFLICT"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _commit_mapped(db: Session) -> None:
    """提交并把唯一性竞争（并发同 code 不同 key）映射为结构化 409。"""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise LocationError(_CODE_CONFLICT, "编号或唯一性约束冲突", 409) from exc


def _ensure_room_code_free(db: Session, code: str, *, exclude_id: int | None = None) -> None:
    q = select(StorageRoom.id).where(StorageRoom.code == code)
    if exclude_id is not None:
        q = q.where(StorageRoom.id != exclude_id)
    if db.scalar(q) is not None:
        raise LocationError(_CODE_CONFLICT, f"房间编号 {code} 已被使用（含已归档）", 409)


def _ensure_shelf_code_free(db: Session, code: str, *, exclude_id: int | None = None) -> None:
    q = select(StorageShelf.id).where(StorageShelf.code == code)
    if exclude_id is not None:
        q = q.where(StorageShelf.id != exclude_id)
    if db.scalar(q) is not None:
        raise LocationError(_CODE_CONFLICT, f"书架编号 {code} 已被使用（含已归档）", 409)


def _ensure_cell_code_free(db: Session, shelf_id: int, code: str,
                           used: set[str], *, exclude_id: int | None = None) -> None:
    """cell code 书架内唯一且不复用：查库（含已归档）+ 本批已占集合。"""
    if code in used:
        raise LocationError(_CODE_CONFLICT, f"格子编号 {code} 在本批布局中重复", 409)
    q = select(ShelfCell.id).where(ShelfCell.shelf_id == shelf_id, ShelfCell.code == code)
    if exclude_id is not None:
        q = q.where(ShelfCell.id != exclude_id)
    if db.scalar(q) is not None:
        raise LocationError(_CODE_CONFLICT, f"格子编号 {code} 已被使用（含已归档，编号不复用）", 409)


def _next_cell_code(used: set[str]) -> str:
    n = 1
    while f"C{n}" in used:
        n += 1
    return f"C{n}"


# ── 副本引用检查（含 lent_out/lost 等非在架状态，§7.1） ──

def _copy_refs_by_shelf_ids(db: Session, shelf_ids: list[int]) -> list[int]:
    if not shelf_ids:
        return []
    return list(db.scalars(
        select(BookCopy.id).where(BookCopy.placement_shelf_id.in_(shelf_ids))
    ).all())


def _copy_refs_by_cell_ids(db: Session, cell_ids: list[int]) -> list[int]:
    if not cell_ids:
        return []
    return list(db.scalars(
        select(BookCopy.id).where(BookCopy.placement_cell_id.in_(cell_ids))
    ).all())


def _raise_if_referenced(refs: list[int], what: str) -> None:
    if refs:
        sample = ", ".join(str(i) for i in sorted(refs)[:5])
        raise LocationInUse(f"{what}仍被 {len(refs)} 个副本引用（copy_id: {sample}），请先处理副本")


def _shelf_ids_of_room(db: Session, room_id: int) -> list[int]:
    return list(db.scalars(select(StorageShelf.id).where(StorageShelf.room_id == room_id)).all())


def _cell_ids_of_layer(db: Session, layer_id: int) -> list[int]:
    return list(db.scalars(select(ShelfCell.id).where(ShelfCell.layer_id == layer_id)).all())


def _cell_ids_of_shelf(db: Session, shelf_id: int) -> list[int]:
    return list(db.scalars(select(ShelfCell.id).where(ShelfCell.shelf_id == shelf_id)).all())


# ── 读 ──

def list_rooms(db: Session, *, include_archived: bool, limit: int, offset: int) -> dict:
    q = select(StorageRoom)
    if not include_archived:
        q = q.where(StorageRoom.archived_at.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(StorageRoom.sort_order, StorageRoom.id)
                      .limit(limit).offset(offset)).all()
    return {"items": [RoomOut.model_validate(r).model_dump() for r in rows], "total": total}


def list_shelves(db: Session, *, room_id: int | None, include_archived: bool,
                 limit: int, offset: int) -> dict:
    q = select(StorageShelf)
    if room_id is not None:
        q = q.where(StorageShelf.room_id == room_id)
    if not include_archived:
        q = q.where(StorageShelf.archived_at.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(StorageShelf.sort_order, StorageShelf.id)
                      .limit(limit).offset(offset)).all()
    return {"items": [ShelfOut.model_validate(r).model_dump() for r in rows], "total": total}


def shelf_detail_dict(db: Session, shelf: StorageShelf, *, include_archived: bool = False) -> dict:
    # mode="json"：与 FastAPI 响应序列化一致，保证幂等回执与实时响应逐字节同形
    layer_q = select(ShelfLayer).where(ShelfLayer.shelf_id == shelf.id)
    if not include_archived:
        layer_q = layer_q.where(ShelfLayer.archived_at.is_(None))
    layers = db.scalars(layer_q.order_by(ShelfLayer.sort_order, ShelfLayer.id)).all()
    layer_outs: list[dict] = []
    for layer in layers:
        cell_q = select(ShelfCell).where(ShelfCell.layer_id == layer.id)
        if not include_archived:
            cell_q = cell_q.where(ShelfCell.archived_at.is_(None))
        cells = db.scalars(cell_q.order_by(ShelfCell.sort_order, ShelfCell.id)).all()
        layer_outs.append({
            **LayerDetailOut.model_validate(layer).model_dump(mode="json", exclude={"cells"}),
            "cells": [CellOut.model_validate(c).model_dump(mode="json") for c in cells],
        })
    return {
        **ShelfDetailOut.model_validate(shelf).model_dump(mode="json", exclude={"layers"}),
        "layers": layer_outs,
    }


def get_shelf_detail(db: Session, shelf_id: int, *, include_archived: bool = False) -> dict:
    shelf = db.get(StorageShelf, shelf_id)
    if shelf is None:
        raise NotFound(f"书架 {shelf_id} 不存在")
    return shelf_detail_dict(db, shelf, include_archived=include_archived)


def get_operation_receipt(db: Session, idempotency_key: str, *, operator_member_id: int) -> dict:
    """回执查询（LOC-02）：区分 completed / not_found；not_found 不等于「确定未执行」。"""
    op = db.scalar(select(LocationOperation).where(
        LocationOperation.operator_member_id == operator_member_id,
        LocationOperation.idempotency_key == idempotency_key,
    ))
    if op is None:
        return {"idempotency_key": idempotency_key, "status": "not_found"}
    if op.result_json is None:
        # 登记与结果同事务提交，正常不会出现；防御性返回 pending
        return {"idempotency_key": idempotency_key, "status": "pending",
                "created_at": op.created_at}
    return {
        "idempotency_key": idempotency_key,
        "status": "completed",
        "result": json.loads(op.result_json),
        "created_at": op.created_at,
    }


# ── 写：房间 ──

def create_room(db: Session, payload: RoomCreate, *, operator_member_id: int) -> dict:
    digest_payload = {"op": "storage.room.create",
                      "payload": payload.model_dump(exclude={"idempotency_key"})}

    def fn(db: Session) -> dict:
        begin = storage_tx.begin_operation(
            db, operator_member_id=operator_member_id,
            idempotency_key=payload.idempotency_key, payload=digest_payload)
        if begin.replayed:
            return begin.result
        _ensure_room_code_free(db, payload.code)
        room = StorageRoom(code=payload.code, name=payload.name,
                           description=payload.description, sort_order=payload.sort_order)
        db.add(room)
        db.flush()
        db.refresh(room)
        log_operation(db, action="storage.room.create", member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id,
                               "room_id": room.id, "code": room.code})
        result = {"room": RoomOut.model_validate(room).model_dump(mode="json")}
        storage_tx.complete_operation(db, begin.operation, result)
        _commit_mapped(db)
        return result

    return storage_tx.run_in_txn(db, fn)


def update_room(db: Session, room_id: int, payload: RoomUpdate, *,
                operator_member_id: int) -> dict:
    fields = payload.model_dump(exclude_unset=True, exclude={"idempotency_key"})
    digest_payload = {"op": "storage.room.update", "room_id": room_id, "fields": fields}

    def fn(db: Session) -> dict:
        begin = None
        if payload.idempotency_key:
            begin = storage_tx.begin_operation(
                db, operator_member_id=operator_member_id,
                idempotency_key=payload.idempotency_key, payload=digest_payload)
            if begin.replayed:
                return begin.result
        resources = storage_tx.collect_ancestors(db, room_ids=[room_id])
        storage_tx.lock_resources(db, resources)
        room = db.get(StorageRoom, room_id)

        action = "storage.room.update"
        edits = {k: v for k, v in fields.items() if k in ("code", "name", "description", "sort_order")}
        if edits and room.archived_at is not None:
            raise LocationArchived(f"房间 {room_id} 已归档，仅可恢复")
        if "code" in edits:
            _ensure_room_code_free(db, edits["code"], exclude_id=room_id)
        if fields.get("archived") is True:
            if room.archived_at is not None:
                raise LocationArchived(f"房间 {room_id} 已是归档状态")
            _raise_if_referenced(_copy_refs_by_shelf_ids(db, _shelf_ids_of_room(db, room_id)),
                                 f"房间 {room_id} ")
            action = "storage.room.archive"
        elif fields.get("archived") is False and room.archived_at is not None:
            action = "storage.room.restore"  # room 无祖先，可直接恢复

        storage_tx.bump_version(db, StorageRoom, room_id, payload.version)
        for key, value in edits.items():
            setattr(room, key, value)
        if fields.get("archived") is True:
            room.archived_at = _now()
        elif fields.get("archived") is False:
            room.archived_at = None
        db.flush()
        db.refresh(room)
        log_operation(db, action=action, member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id if begin else None,
                               "room_id": room_id, "fields": fields,
                               "version": payload.version})
        result = {"room": RoomOut.model_validate(room).model_dump(mode="json")}
        if begin is not None:
            storage_tx.complete_operation(db, begin.operation, result)
        _commit_mapped(db)
        return result

    return storage_tx.run_in_txn(db, fn)


# ── 写：书架（含初始层格结构） ──

def _create_layer_with_cells(db: Session, shelf_id: int, spec: LayerInput | None,
                             position: int, used_codes: set[str]) -> ShelfLayer:
    label = spec.label if spec and spec.label else f"第 {position + 1} 层"
    sort_order = spec.sort_order if spec and spec.sort_order is not None else position
    layer = ShelfLayer(shelf_id=shelf_id, label=label, sort_order=sort_order)
    db.add(layer)
    db.flush()
    cell_specs = spec.cells if spec and spec.cells else [None]  # 默认每层 1 格
    for j, cell_spec in enumerate(cell_specs):
        code = cell_spec.code if cell_spec and cell_spec.code else _next_cell_code(used_codes)
        _ensure_cell_code_free(db, shelf_id, code, used_codes)
        used_codes.add(code)
        cell = ShelfCell(
            shelf_id=shelf_id, layer_id=layer.id, code=code,
            label=cell_spec.label if cell_spec and cell_spec.label else f"格 {j + 1}",
            sort_order=cell_spec.sort_order if cell_spec and cell_spec.sort_order is not None else j,
        )
        db.add(cell)
    db.flush()
    return layer


def create_shelf(db: Session, payload: ShelfCreate, *, operator_member_id: int) -> dict:
    digest_payload = {"op": "storage.shelf.create",
                      "payload": payload.model_dump(exclude={"idempotency_key"})}

    def fn(db: Session) -> dict:
        begin = storage_tx.begin_operation(
            db, operator_member_id=operator_member_id,
            idempotency_key=payload.idempotency_key, payload=digest_payload)
        if begin.replayed:
            return begin.result
        resources = storage_tx.collect_ancestors(db, room_ids=[payload.room_id])
        storage_tx.lock_resources(db, resources)
        room = db.get(StorageRoom, payload.room_id)
        if room.archived_at is not None:
            raise LocationArchived(f"房间 {payload.room_id} 已归档，不能新建书架")
        _ensure_shelf_code_free(db, payload.code)
        shelf = StorageShelf(room_id=payload.room_id, code=payload.code, name=payload.name,
                             position_note=payload.position_note, sort_order=payload.sort_order)
        db.add(shelf)
        db.flush()
        used_codes: set[str] = set()
        layer_specs = payload.layers if payload.layers else [None]  # 默认 1 层 1 格
        for i, spec in enumerate(layer_specs):
            _create_layer_with_cells(db, shelf.id, spec, i, used_codes)
        db.refresh(shelf)
        # 不 bump room 版本：新建子资源不改变 room 自身的乐观锁口径
        log_operation(db, action="storage.shelf.create", member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id,
                               "shelf_id": shelf.id, "room_id": payload.room_id,
                               "layer_count": len(layer_specs)})
        result = {"shelf": shelf_detail_dict(db, shelf)}
        storage_tx.complete_operation(db, begin.operation, result)
        _commit_mapped(db)
        return result

    return storage_tx.run_in_txn(db, fn)


def update_shelf(db: Session, shelf_id: int, payload: ShelfUpdate, *,
                 operator_member_id: int) -> dict:
    fields = payload.model_dump(exclude_unset=True, exclude={"idempotency_key"})
    digest_payload = {"op": "storage.shelf.update", "shelf_id": shelf_id, "fields": fields}

    def fn(db: Session) -> dict:
        begin = None
        if payload.idempotency_key:
            begin = storage_tx.begin_operation(
                db, operator_member_id=operator_member_id,
                idempotency_key=payload.idempotency_key, payload=digest_payload)
            if begin.replayed:
                return begin.result
        resources = storage_tx.collect_ancestors(db, shelf_ids=[shelf_id])
        storage_tx.lock_resources(db, resources)
        shelf = db.get(StorageShelf, shelf_id)
        room = db.get(StorageRoom, shelf.room_id)

        action = "storage.shelf.update"
        edits = {k: v for k, v in fields.items()
                 if k in ("code", "name", "position_note", "sort_order")}
        if edits:
            if room.archived_at is not None:
                raise LocationArchived(f"上级房间 {room.id} 已归档，书架只读")
            if shelf.archived_at is not None:
                raise LocationArchived(f"书架 {shelf_id} 已归档，仅可恢复")
        if "code" in edits:
            _ensure_shelf_code_free(db, edits["code"], exclude_id=shelf_id)
        if fields.get("archived") is True:
            if shelf.archived_at is not None:
                raise LocationArchived(f"书架 {shelf_id} 已是归档状态")
            if room.archived_at is not None:
                raise LocationArchived(f"上级房间 {room.id} 已归档")
            _raise_if_referenced(_copy_refs_by_shelf_ids(db, [shelf_id]), f"书架 {shelf_id} ")
            action = "storage.shelf.archive"
        elif fields.get("archived") is False and shelf.archived_at is not None:
            if room.archived_at is not None:
                raise LocationArchived(f"上级房间 {room.id} 已归档，需先恢复房间")
            action = "storage.shelf.restore"

        storage_tx.bump_version(db, StorageShelf, shelf_id, payload.version)
        for key, value in edits.items():
            setattr(shelf, key, value)
        if fields.get("archived") is True:
            shelf.archived_at = _now()
        elif fields.get("archived") is False:
            shelf.archived_at = None
        db.flush()
        db.refresh(shelf)
        log_operation(db, action=action, member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id if begin else None,
                               "shelf_id": shelf_id, "fields": fields,
                               "version": payload.version})
        result = {"shelf": shelf_detail_dict(db, shelf)}
        if begin is not None:
            storage_tx.complete_operation(db, begin.operation, result)
        _commit_mapped(db)
        return result

    return storage_tx.run_in_txn(db, fn)



# ── 写：布局（PUT，稳定 ID 全量表达） ──

def put_layout(db: Session, shelf_id: int, payload: LayoutPut, *,
               operator_member_id: int) -> dict:
    digest_payload = {"op": "storage.shelf.layout", "shelf_id": shelf_id,
                      "payload": payload.model_dump(exclude={"idempotency_key"})}

    def fn(db: Session) -> dict:
        begin = storage_tx.begin_operation(
            db, operator_member_id=operator_member_id,
            idempotency_key=payload.idempotency_key, payload=digest_payload)
        if begin.replayed:
            return begin.result

        # 锁集合：书架完整祖先 + 全部现存层/格（§7.3 固定锁序，不持锁补锁）
        resources = storage_tx.collect_ancestors(db, shelf_ids=[shelf_id])
        resources["layer"] = sorted(set(resources["layer"]) | set(
            db.scalars(select(ShelfLayer.id).where(ShelfLayer.shelf_id == shelf_id)).all()))
        resources["cell"] = sorted(set(resources["cell"]) | set(
            _cell_ids_of_shelf(db, shelf_id)))
        storage_tx.lock_resources(db, resources)

        shelf = db.get(StorageShelf, shelf_id)
        room = db.get(StorageRoom, shelf.room_id)
        if room.archived_at is not None:
            raise LocationArchived(f"上级房间 {room.id} 已归档，书架只读")
        if shelf.archived_at is not None:
            raise LocationArchived(f"书架 {shelf_id} 已归档，需先恢复再调整布局")

        existing_layers = {l.id: l for l in db.scalars(
            select(ShelfLayer).where(ShelfLayer.shelf_id == shelf_id)).all()}
        existing_cells = {c.id: c for c in db.scalars(
            select(ShelfCell).where(ShelfCell.shelf_id == shelf_id)).all()}
        used_codes = {c.code for c in existing_cells.values()}
        seen_layers: set[int] = set()
        seen_cells: set[int] = set()
        stats = {"layers_added": 0, "layers_archived": 0, "cells_added": 0, "cells_archived": 0}

        def archive_cell(cell: ShelfCell) -> None:
            # 归档检查所有副本引用（含外借/遗失），引用在则 409，cell 不变
            _raise_if_referenced(_copy_refs_by_cell_ids(db, [cell.id]),
                                 f"格子 {cell.id} ")
            cell.archived_at = _now()
            storage_tx.bump_version(db, ShelfCell, cell.id, cell.version)
            db.flush()
            db.refresh(cell)
            stats["cells_archived"] += 1

        def archive_layer(layer: ShelfLayer) -> None:
            _raise_if_referenced(
                _copy_refs_by_cell_ids(db, _cell_ids_of_layer(db, layer.id)),
                f"层 {layer.id} ")
            layer.archived_at = _now()
            storage_tx.bump_version(db, ShelfLayer, layer.id, layer.version)
            db.flush()
            db.refresh(layer)
            stats["layers_archived"] += 1

        for position, layer_in in enumerate(payload.layers):
            if layer_in.id is not None:
                layer = existing_layers.get(layer_in.id)
                if layer is None:
                    probe = db.get(ShelfLayer, layer_in.id)
                    if probe is None:
                        raise NotFound(f"层 {layer_in.id} 不存在")
                    raise InvalidRelation(f"层 {layer_in.id} 不属于书架 {shelf_id}")
                seen_layers.add(layer_in.id)
                # 期望版本：任何变更前一次性校验，之后本实体只 bump 一次
                if layer_in.version is not None and layer.version != layer_in.version:
                    raise storage_tx.PlacementChanged(
                        f"层 {layer.id} 期望版本 {layer_in.version} 已失效")
                changed = False
                if (layer_in.label is not None or layer_in.sort_order is not None) \
                        and layer.archived_at is not None:
                    raise LocationArchived(f"层 {layer.id} 已归档，仅可恢复")
                if layer_in.archived is True:
                    archive_layer(layer)  # 内部含已归档判重与 bump
                else:
                    if layer_in.archived is False and layer.archived_at is not None:
                        layer.archived_at = None  # 祖先书架已确认未归档
                        changed = True
                    if layer_in.label is not None:
                        layer.label = layer_in.label
                        changed = True
                    if layer_in.sort_order is not None:
                        layer.sort_order = layer_in.sort_order
                        changed = True
                    if changed:
                        storage_tx.bump_version(db, ShelfLayer, layer.id, layer.version)
                        db.flush()
                        db.refresh(layer)
            else:
                layer = ShelfLayer(
                    shelf_id=shelf_id,
                    label=layer_in.label or f"第 {position + 1} 层",
                    sort_order=layer_in.sort_order if layer_in.sort_order is not None else position,
                )
                db.add(layer)
                db.flush()
                existing_layers[layer.id] = layer
                seen_layers.add(layer.id)
                stats["layers_added"] += 1

            if layer_in.cells is None:
                continue  # 不动该层格子
            if layer.archived_at is not None:
                raise LocationArchived(f"层 {layer.id} 已归档，不能调整格子")
            layer_cells = {c.id: c for c in existing_cells.values() if c.layer_id == layer.id}
            for j, cell_in in enumerate(layer_in.cells):
                if cell_in.id is not None:
                    cell = existing_cells.get(cell_in.id)
                    if cell is None:
                        probe = db.get(ShelfCell, cell_in.id)
                        if probe is None:
                            raise NotFound(f"格子 {cell_in.id} 不存在")
                        raise InvalidRelation(f"格子 {cell_in.id} 不属于书架 {shelf_id}")
                    if cell.layer_id != layer.id:
                        raise InvalidRelation(
                            f"格子 {cell.id} 属于层 {cell.layer_id}，不能挂到层 {layer.id}")
                    seen_cells.add(cell.id)
                    if cell_in.version is not None and cell.version != cell_in.version:
                        raise storage_tx.PlacementChanged(
                            f"格子 {cell.id} 期望版本 {cell_in.version} 已失效")
                    changed = False
                    if (cell_in.label is not None or cell_in.sort_order is not None
                            or cell_in.code is not None) and cell.archived_at is not None:
                        raise LocationArchived(f"格子 {cell.id} 已归档，仅可恢复")
                    if cell_in.archived is True:
                        archive_cell(cell)  # 内部含判重、引用检查与 bump
                    else:
                        if cell_in.archived is False and cell.archived_at is not None:
                            cell.archived_at = None  # 层与书架均已确认未归档
                            changed = True
                        if cell_in.code is not None and cell_in.code != cell.code:
                            _ensure_cell_code_free(db, shelf_id, cell_in.code, used_codes,
                                                   exclude_id=cell.id)
                            used_codes.discard(cell.code)
                            used_codes.add(cell_in.code)
                            cell.code = cell_in.code
                            changed = True
                        if cell_in.label is not None:
                            cell.label = cell_in.label
                            changed = True
                        if cell_in.sort_order is not None:
                            cell.sort_order = cell_in.sort_order
                            changed = True
                        if changed:
                            storage_tx.bump_version(db, ShelfCell, cell.id, cell.version)
                            db.flush()
                            db.refresh(cell)
                else:
                    code = cell_in.code or _next_cell_code(used_codes)
                    _ensure_cell_code_free(db, shelf_id, code, used_codes)
                    used_codes.add(code)
                    cell = ShelfCell(
                        shelf_id=shelf_id, layer_id=layer.id, code=code,
                        label=cell_in.label or f"格 {j + 1}",
                        sort_order=cell_in.sort_order if cell_in.sort_order is not None else j,
                    )
                    db.add(cell)
                    db.flush()
                    existing_cells[cell.id] = cell
                    seen_cells.add(cell.id)
                    stats["cells_added"] += 1
            # 全量语义：该层未列出的现存活动格子归档（有引用则 409）
            for cid, cell in layer_cells.items():
                if cid not in seen_cells and cell.archived_at is None:
                    archive_cell(cell)

        # 全量语义：未列出的现存活动层归档（有引用则 409）
        for lid, layer in existing_layers.items():
            if lid not in seen_layers and layer.archived_at is None:
                archive_layer(layer)

        storage_tx.bump_version(db, StorageShelf, shelf_id, payload.shelf_version)
        db.flush()
        db.refresh(shelf)
        log_operation(db, action="storage.shelf.layout", member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id,
                               "shelf_id": shelf_id, "shelf_version": payload.shelf_version,
                               **stats})
        result = {"shelf": shelf_detail_dict(db, shelf)}
        storage_tx.complete_operation(db, begin.operation, result)
        _commit_mapped(db)
        return result

    return storage_tx.run_in_txn(db, fn)
