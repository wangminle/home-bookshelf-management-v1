"""LOC-07：位置域写事务基础——幂等回执、锁序、版本乐观锁、受限重试。

契约依据：design/checkpoints/实体书架位置管理-M0契约冻结-20261006.md

- 幂等（LOC-02）：同 key 同摘要重放已存回执；同 key 不同摘要 409
  IDEMPOTENCY_CONFLICT；写事务开始即登记 key，与业务修改同事务提交；
  并发同 key 等待首事务完成后读回执（SQLite 快照冲突由 run_in_txn 重试承接）。
- 锁序（LOC-03）：room → shelf → layer → cell → copy，类内按 ID 升序；
  SQLite 用零改写条件 UPDATE 取写锁并重读，PostgreSQL 用 SELECT FOR UPDATE
  （同等资源锁策略；本机无 PG，该分支未实机验证）。
- 版本乐观锁（LOC-02）：条件更新期望版本，rowcount=0 → 409 PLACEMENT_CHANGED，
  版本冲突不重试。
- 重试（LOC-03）：仅忙/死锁（OperationalError）在确定整体回滚后从头重试
  ≤3 次；业务冲突（LocationError/IntegrityError 等）不重试。
- 审计纪律（LOC-03 事务边界）：位置写路径只用 log_operation（add 不 commit），
  禁用 log_and_commit；审计与业务修改、幂等回执同事务，审计失败即整体失败。
  本模块自身不 commit——事务边界由调用方（fn 内显式 commit / run_in_txn 回滚）控制。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import (
    BookCopy,
    ShelfCell,
    ShelfLayer,
    StorageRoom,
    StorageShelf,
)
from app.models.storage import LocationOperation

T = TypeVar("T")


# ── 错误类型（LOC-02 错误码冻结） ──

class LocationError(Exception):
    """位置域业务错误：API 层映射为 detail={"code":…, "message":…} 的结构化响应。

    结构化 detail 仅限位置域新契约；旧接口的字符串 detail 约定不动。
    """

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=self.status_code,
            detail={"code": self.code, "message": self.message},
        )


class IdempotencyConflict(LocationError):
    def __init__(self, message: str = "同一幂等键提交了不同的载荷，拒绝执行") -> None:
        super().__init__("IDEMPOTENCY_CONFLICT", message, 409)


class PlacementChanged(LocationError):
    def __init__(self, message: str = "位置或版本已被他人修改，请刷新后重试") -> None:
        super().__init__("PLACEMENT_CHANGED", message, 409)


class LocationArchived(LocationError):
    def __init__(self, message: str = "目标位置已归档，不可再写入") -> None:
        super().__init__("LOCATION_ARCHIVED", message, 409)


class LocationInUse(LocationError):
    def __init__(self, message: str = "位置仍被副本或子结构引用，不可删除") -> None:
        super().__init__("LOCATION_IN_USE", message, 409)


class InvalidRelation(LocationError):
    def __init__(self, message: str = "层格父子关系不合法") -> None:
        super().__init__("INVALID_RELATION", message, 422)


class NotFound(LocationError):
    def __init__(self, message: str = "位置资源不存在") -> None:
        super().__init__("NOT_FOUND", message, 404)


# ── 幂等回执 ──

def payload_digest(payload: Any) -> str:
    """规范化 JSON（sort_keys、紧凑分隔符、UTF-8）+ sha256 十六进制。"""
    canonical = json.dumps(
        payload if payload is not None else {},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class OperationBegin:
    """begin_operation 结果。replayed=True 时调用方不得再执行业务写入，
    直接以 result（已存回执）响应。"""

    operation: LocationOperation
    replayed: bool
    result: dict | None


def _find_operation(db: Session, operator_member_id: int, key: str) -> LocationOperation | None:
    return db.scalar(
        select(LocationOperation).where(
            LocationOperation.operator_member_id == operator_member_id,
            LocationOperation.idempotency_key == key,
        )
    )


def _replay_or_conflict(existing: LocationOperation, digest: str) -> OperationBegin:
    if existing.payload_digest != digest:
        raise IdempotencyConflict()
    result = json.loads(existing.result_json) if existing.result_json else None
    return OperationBegin(operation=existing, replayed=True, result=result)


def begin_operation(
    db: Session,
    *,
    operator_member_id: int,
    idempotency_key: str,
    payload: Any,
) -> OperationBegin:
    """写事务开始即登记幂等键（与业务修改同事务提交）。

    - 同 key 同摘要：重放已存回执（result_json 为空表示首事务尚未完成，
      按契约客户端应等待重查，这里原样返回 None 回执）；
    - 同 key 不同摘要：409 IDEMPOTENCY_CONFLICT；
    - 并发同 key：INSERT 阻塞/冲突后回滚本事务登记，重读已提交回执重放。

    必须在事务起始调用（函数内的冲突恢复会 rollback 当前事务）。
    operator_member_id 必填：唯一约束锚定 (operator, key)，NULL 在
    SQLite 唯一索引下互不相等，会破坏幂等语义。
    """
    digest = payload_digest(payload)
    existing = _find_operation(db, operator_member_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, digest)

    op = LocationOperation(
        operator_member_id=operator_member_id,
        idempotency_key=idempotency_key,
        payload_digest=digest,
    )
    db.add(op)
    try:
        db.flush()
    except IntegrityError:
        # 并发同 key 且首事务已提交：回滚本事务登记（此刻尚无业务写入），
        # 用新快照读回执；首事务已回滚的罕见情况重新抛出让上层处理。
        db.rollback()
        existing = _find_operation(db, operator_member_id, idempotency_key)
        if existing is None:
            raise
        return _replay_or_conflict(existing, digest)
    return OperationBegin(operation=op, replayed=False, result=None)


def complete_operation(db: Session, operation: LocationOperation, result: dict) -> None:
    """登记回执（同事务提交，不单独 commit）。"""
    operation.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    db.flush()


# ── 祖先收集与锁序 ──

def collect_ancestors(
    db: Session,
    *,
    room_ids: Iterable[int] = (),
    shelf_ids: Iterable[int] = (),
    layer_ids: Iterable[int] = (),
    cell_ids: Iterable[int] = (),
    copy_ids: Iterable[int] = (),
) -> dict[str, list[int]]:
    """收集源与目标位置的完整祖先集合（LOC-03）。

    显式给出的 ID 即使行不存在也保留在结果中（由 lock_resources 报 404）；
    祖先缺失的行静默跳过（锁定阶段统一校验存在性与父子关系）。
    """
    rooms, shelves = set(room_ids), set(shelf_ids)
    layers, cells, copies = set(layer_ids), set(cell_ids), set(copy_ids)

    if copies:
        for copy in db.scalars(select(BookCopy).where(BookCopy.id.in_(copies))):
            if copy.placement_shelf_id is not None:
                shelves.add(copy.placement_shelf_id)
            if copy.placement_cell_id is not None:
                cells.add(copy.placement_cell_id)
    if cells:
        for cell in db.scalars(select(ShelfCell).where(ShelfCell.id.in_(cells))):
            shelves.add(cell.shelf_id)
            layers.add(cell.layer_id)
    if layers:
        for layer in db.scalars(select(ShelfLayer).where(ShelfLayer.id.in_(layers))):
            shelves.add(layer.shelf_id)
    if shelves:
        for shelf in db.scalars(select(StorageShelf).where(StorageShelf.id.in_(shelves))):
            rooms.add(shelf.room_id)

    return {
        "room": sorted(rooms),
        "shelf": sorted(shelves),
        "layer": sorted(layers),
        "cell": sorted(cells),
        "copy": sorted(copies),
    }


# 锁序（LOC-03）：room → shelf → layer → cell → copy。
# 每项 (资源名, 模型, 零改写锁列)——BookCopy 无 version 列，用 placement_version。
_LOCK_ORDER: tuple[tuple[str, type, str], ...] = (
    ("room", StorageRoom, "version"),
    ("shelf", StorageShelf, "version"),
    ("layer", ShelfLayer, "version"),
    ("cell", ShelfCell, "version"),
    ("copy", BookCopy, "placement_version"),
)


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def lock_resources(db: Session, resources: dict[str, Iterable[int]]) -> None:
    """按冻结锁序对资源加事务写锁；资源不存在 → 404 NotFound。

    SQLite：逐行零改写条件 UPDATE（锁列与 updated_at 自赋值，不改任何数据）
    取写锁并 expire 重读；PostgreSQL：同类一次 SELECT … ORDER BY id FOR UPDATE
    （本机无 PG，该分支未实机验证）。
    调用方在锁定后自行重验父子关系、版本与祖先集合；不符即整体回滚，
    本函数不做持锁补锁。
    """
    for kind, model, lock_column in _LOCK_ORDER:
        ids = sorted(set(resources.get(kind) or ()))
        if not ids:
            continue
        if _is_postgres(db):
            rows = db.scalars(
                select(model).where(model.id.in_(ids)).order_by(model.id).with_for_update()
            ).all()
            if len({r.id for r in rows}) != len(ids):
                raise NotFound(f"{kind} 资源不存在: {sorted(set(ids) - {r.id for r in rows})}")
            continue
        column = getattr(model, lock_column)
        for row_id in ids:
            result = db.execute(
                update(model)
                .execution_options(synchronize_session=False)
                .where(model.id == row_id)
                # 显式自赋值 updated_at，抑制 mixin onupdate，保证零改写
                .values(**{lock_column: column, "updated_at": model.updated_at})
            )
            if result.rowcount != 1:
                raise NotFound(f"{kind} 资源不存在: {row_id}")
        # 清除锁前 ORM 缓存，后续重读看到已提交内容（BUG-285 同口径）
        db.expire_all()


# ── 版本乐观锁 ──

def bump_version(
    db: Session,
    model: type,
    row_id: int,
    expected_version: int,
    *,
    column: str = "version",
) -> None:
    """条件更新版本（column + 1 WHERE id AND 期望版本）；rowcount=0 → 409 PLACEMENT_CHANGED。

    副本定位传 column="placement_version"（BookCopy 的版本列）。
    """
    version_col = getattr(model, column)
    result = db.execute(
        update(model)
        .execution_options(synchronize_session=False)
        .where(model.id == row_id, version_col == expected_version)
        .values(**{column: version_col + 1, "updated_at": model.updated_at})
    )
    if result.rowcount != 1:
        raise PlacementChanged(f"{model.__tablename__}#{row_id} 期望版本 {expected_version} 已失效")


# ── 受限重试 ──

def run_in_txn(db: Session, fn: Callable[[Session], T], *, max_retries: int = 3) -> T:
    """执行位置写事务：仅 OperationalError（忙/死锁）在确定整体回滚后从头重试，
    最多重试 max_retries 次（合计最多 1 + max_retries 次执行）。

    fn 自己控制 commit；LocationError 等业务冲突直接抛出，不重试、不代为回滚
    （回滚责任在调用方/依赖收口）。
    """
    attempts = 0
    while True:
        try:
            return fn(db)
        except OperationalError:
            db.rollback()
            attempts += 1
            if attempts > max_retries:
                raise
