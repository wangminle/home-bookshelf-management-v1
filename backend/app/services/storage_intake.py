"""LOC-14：存量实体副本补录领域服务（设计 §7.2/§10.1，M0 契约修订 LOC-14）。

纪律：
- 不自动创建实体副本（LOC-01 冻结）：新增册数只来自请求中逐项显式确认的
  new_copies.count；已有副本优先分配位置（assign），不重复建册。
- 事务边界（LOC-03 冻结）：不复用会自行 commit 的 create_copy——副本创建、
  位置设置、幂等回执、审计在同一事务，由本模块 fn 显式 commit；旧
  POST /books/{id}/copies 的提交语义不变。
- 原子性：任一条目失效（书目/成员不存在、副本不属于该书目、电子副本、
  目标归档、版本冲突）整批回滚零写入；≤100 册口径 = 全请求 assign 条数
  与新增册数合计（契约修订 LOC-14）。
- 不改变 reading_progress、reading_notes、purchase_records（本模块不触及）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Book, BookCopy, Member
from app.schemas.storage import IntakeCommitIn
from app.services import storage_tx
from app.services.storage_placement import (
    BATCH_MAX_COPIES,
    InvalidBatch,
    _display,
    _ensure_physical,
    _load_and_validate_target,
)
from app.services.storage_tx import NotFound
from app.utils.operation_log import log_operation


def _validate_intake_shape(payload: IntakeCommitIn) -> None:
    book_ids = [item.book_id for item in payload.items]
    if len(set(book_ids)) != len(book_ids):
        raise InvalidBatch("批次内 book_id 重复：同一书目的分配与新增合并在同一条目")
    assign_ids = [a.copy_id for item in payload.items for a in (item.assign or [])]
    if len(set(assign_ids)) != len(assign_ids):
        raise InvalidBatch("批次内 assign 的 copy_id 重复")
    total = len(assign_ids) + sum(
        item.new_copies.count for item in payload.items if item.new_copies)
    if total > BATCH_MAX_COPIES:
        raise InvalidBatch(
            f"每批最多 {BATCH_MAX_COPIES} 册（assign 与新增合计），本批 {total} 册")


def commit_intake(
    db: Session,
    payload: IntakeCommitIn,
    *,
    operator_member_id: int,
) -> dict:
    """批量补录：为已有副本分配位置 + 显式确认新增实体副本，单事务原子提交。"""
    _validate_intake_shape(payload)
    digest_payload = {"op": "storage.copies.intake",
                      "items": payload.model_dump(exclude={"idempotency_key"})}

    def fn(db: Session) -> dict:
        begin = storage_tx.begin_operation(
            db, operator_member_id=operator_member_id,
            idempotency_key=payload.idempotency_key, payload=digest_payload)
        if begin.replayed:
            return begin.result

        # 收集全部源副本与所有目标的完整祖先集合（多目标：独立条目各自携带目标），
        # 一次按固定锁序加锁；锁定后重验，不符即整体回滚，不持锁补锁（§7.3）。
        assign_ids = [a.copy_id for item in payload.items for a in (item.assign or [])]
        targets = [a.target for item in payload.items for a in (item.assign or [])]
        targets += [item.new_copies.target for item in payload.items
                    if item.new_copies is not None]
        resources = storage_tx.collect_ancestors(
            db,
            shelf_ids=[t.shelf_id for t in targets],
            cell_ids=[t.cell_id for t in targets if t.cell_id is not None],
            copy_ids=assign_ids)
        storage_tx.lock_resources(db, resources)

        cache: dict = {}
        items_out: list[dict] = []
        audit_items: list[dict] = []
        total_assigned = total_created = 0
        for item in payload.items:
            book = db.get(Book, item.book_id)
            if book is None:
                raise NotFound(f"书目 {item.book_id} 不存在")
            assigned: list[dict] = []
            audit_assigned: list[dict] = []
            for entry in item.assign or []:
                copy = db.get(BookCopy, entry.copy_id)  # 存在性已由锁阶段保证
                if copy.book_id != item.book_id:
                    raise NotFound(f"副本 {entry.copy_id} 不属于书目 {item.book_id}")
                _ensure_physical(copy)
                shelf, cell = _load_and_validate_target(
                    db, entry.target.shelf_id, entry.target.cell_id)
                from_shelf, from_cell = copy.placement_shelf_id, copy.placement_cell_id
                from_display = _display(db, from_shelf, from_cell, cache)
                storage_tx.bump_version(db, BookCopy, copy.id,
                                        entry.placement_version,
                                        column="placement_version")
                copy.placement_shelf_id = shelf.id
                copy.placement_cell_id = cell.id if cell else None
                db.flush()
                db.refresh(copy)
                assigned.append({"copy_id": copy.id,
                                 "placement_version": copy.placement_version})
                audit_assigned.append({
                    "copy_id": copy.id,
                    "from": {"shelf_id": from_shelf, "cell_id": from_cell,
                             "display": from_display},
                    "to": {"shelf_id": shelf.id, "cell_id": cell.id if cell else None,
                           "display": _display(db, shelf.id,
                                               cell.id if cell else None, cache)},
                    "from_version": entry.placement_version,
                    "to_version": copy.placement_version})
            created: list[int] = []
            audit_created: dict | None = None
            if item.new_copies is not None:
                new = item.new_copies
                member = db.get(Member, new.owner_member_id)
                if member is None:
                    raise NotFound(f"成员 {new.owner_member_id} 不存在")
                shelf, cell = _load_and_validate_target(
                    db, new.target.shelf_id, new.target.cell_id)
                new_rows = []
                for _ in range(new.count):
                    row = BookCopy(
                        book_id=item.book_id, copy_type="physical",
                        owner_member_id=new.owner_member_id,
                        placement_shelf_id=shelf.id,
                        placement_cell_id=cell.id if cell else None)
                    db.add(row)
                    new_rows.append(row)
                db.flush()
                created = [r.id for r in new_rows]
                audit_created = {
                    "count": new.count, "owner_member_id": new.owner_member_id,
                    "copy_ids": created,
                    "to": {"shelf_id": shelf.id, "cell_id": cell.id if cell else None,
                           "display": _display(db, shelf.id,
                                               cell.id if cell else None, cache)}}
            total_assigned += len(assigned)
            total_created += len(created)
            items_out.append({"book_id": item.book_id, "book_title": book.title,
                              "assigned": assigned, "created": created})
            audit_items.append({"book_id": item.book_id,
                                "assigned": audit_assigned,
                                "created": audit_created})

        log_operation(db, action="storage.copies.intake",
                      member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id,
                               "items": audit_items})
        result = {"items": items_out,
                  "assigned": total_assigned, "created": total_created}
        storage_tx.complete_operation(db, begin.operation, result)
        db.commit()
        return result

    return storage_tx.run_in_txn(db, fn)
