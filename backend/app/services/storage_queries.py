"""LOC-08：位置查询、格子副本清单、三类未定位清单与数量统计（只读）。

口径依据：
- 设计文档 §6.3（已分配/在位/在架/涉及书目数；lent_out·lost·discarded·damaged
  不计在位；电子副本不进入实体占用统计）；
- §5.1 未定位三类：有实体副本但位置未登记 / 仅有旧位置文字 / 书目尚无实体副本，
  互不混算（前两类按旧 location 文字是否为空区分，第三类按书目聚合）；
- §5.2 路径展示：「房间 · 书架 · 层 · 格」，分隔符用「 · 」。

路径实时生成（§6.2：不回写生成文本）；归档节点保留在路径中并标注「（已归档）」
——设计文档未明文规定归档节点上的副本位置如何展示，取「展示路径并标记已归档」
（见 M0 契约修订记录）。仅到书架的定位追加「格子待登记」（LOC-01 冻结）。

性能纪律：清单一律 join/聚合查询，禁止逐行查询扩张；路径生成通过调用方共享的
cache 字典在批内去重。
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Book,
    BookCopy,
    Member,
    ShelfCell,
    ShelfLayer,
    StorageRoom,
    StorageShelf,
)
from app.services.storage_tx import NotFound

# §6.3：在位 = in_shelf / storage；在架 = in_shelf
_IN_PLACE_STATUSES = ("in_shelf", "storage")

_PATH_SEP = " · "


# ── 位置路径实时生成 ──

def _marked(name: str, archived_at) -> str:
    """归档节点保留路径并标注。"""
    return f"{name}（已归档）" if archived_at is not None else name


def _shelf_segment(db: Session, shelf_id: int, cache: dict | None) -> dict | None:
    key = ("shelf", shelf_id)
    if cache is not None and key in cache:
        return cache[key]
    shelf = db.get(StorageShelf, shelf_id)
    if shelf is None:
        seg = None
    else:
        room = db.get(StorageRoom, shelf.room_id)
        seg = {
            "room": _marked(room.name, room.archived_at) if room else None,
            "shelf": _marked(shelf.name, shelf.archived_at),
        }
    if cache is not None:
        cache[key] = seg
    return seg


def _cell_segment(db: Session, cell_id: int, cache: dict | None) -> dict | None:
    key = ("cell", cell_id)
    if cache is not None and key in cache:
        return cache[key]
    cell = db.get(ShelfCell, cell_id)
    if cell is None:
        seg = None
    else:
        layer = db.get(ShelfLayer, cell.layer_id)
        seg = {
            "layer": _marked(layer.label, layer.archived_at) if layer else None,
            "cell": _marked(cell.label, cell.archived_at),
        }
    if cache is not None:
        cache[key] = seg
    return seg


def build_location_display(db: Session, shelf_id: int, cell_id: int | None = None,
                           *, cache: dict | None = None) -> str | None:
    """实时拼接位置路径：房间 · 书架 · 层 · 格。

    仅到书架 → 末尾「格子待登记」；归档节点标注「（已归档）」；
    关联行缺失（不应发生，复合 FK 保护）返回 None。
    """
    shelf_seg = _shelf_segment(db, shelf_id, cache)
    if shelf_seg is None:
        return None
    parts = [p for p in (shelf_seg["room"], shelf_seg["shelf"]) if p]
    if cell_id is None:
        parts.append("格子待登记")
    else:
        cell_seg = _cell_segment(db, cell_id, cache)
        if cell_seg is not None:
            parts.extend(p for p in (cell_seg["layer"], cell_seg["cell"]) if p)
    return _PATH_SEP.join(parts)


# ── 数量统计（§6.3 口径，聚合查询不分行） ──

def _placement_stats_by_shelf(db: Session, shelf_ids: list[int]) -> dict[int, dict]:
    if not shelf_ids:
        return {}
    rows = db.execute(
        select(
            BookCopy.placement_shelf_id,
            func.count(),
            func.count().filter(BookCopy.status.in_(_IN_PLACE_STATUSES)),
            func.count().filter(BookCopy.status == "in_shelf"),
            func.count(func.distinct(BookCopy.book_id)),
        )
        .where(BookCopy.placement_shelf_id.in_(shelf_ids))
        .group_by(BookCopy.placement_shelf_id)
    ).all()
    return {
        sid: {
            "assigned_copies": assigned,
            "present_copies": present,
            "on_shelf_copies": on_shelf,
            "books_involved": books,
        }
        for sid, assigned, present, on_shelf, books in rows
    }


def _zero_stats() -> dict:
    return {"assigned_copies": 0, "present_copies": 0,
            "on_shelf_copies": 0, "books_involved": 0}


def shelf_stats(db: Session, shelf_id: int) -> dict:
    return _placement_stats_by_shelf(db, [shelf_id]).get(shelf_id, _zero_stats())


def rooms_stats(db: Session, room_ids: list[int]) -> dict[int, dict]:
    """每房间聚合（两次查询：房间下架、按架聚合副本），不随房间数增长。"""
    if not room_ids:
        return {}
    shelf_rows = db.execute(
        select(StorageShelf.id, StorageShelf.room_id)
        .where(StorageShelf.room_id.in_(room_ids))
    ).all()
    per_shelf = _placement_stats_by_shelf(db, [sid for sid, _ in shelf_rows])
    out: dict[int, dict] = {rid: _zero_stats() for rid in room_ids}
    for sid, rid in shelf_rows:
        stats = per_shelf.get(sid)
        if not stats:
            continue
        acc = out[rid]
        for key in acc:
            acc[key] += stats[key]
    return out


# ── 格子副本清单 ──

def list_cell_copies(db: Session, cell_id: int, *, limit: int, offset: int) -> dict:
    cell = db.get(ShelfCell, cell_id)
    if cell is None:
        raise NotFound(f"格子 {cell_id} 不存在")
    total = db.scalar(
        select(func.count()).select_from(BookCopy)
        .where(BookCopy.placement_cell_id == cell_id)
    ) or 0
    rows = db.execute(
        select(BookCopy, Book.title, Member.name)
        .join(Book, BookCopy.book_id == Book.id)
        .outerjoin(Member, BookCopy.owner_member_id == Member.id)
        .where(BookCopy.placement_cell_id == cell_id)
        .order_by(BookCopy.id)
        .limit(limit).offset(offset)
    ).all()
    display = build_location_display(db, cell.shelf_id, cell_id)
    items = [
        {
            "copy_id": copy.id,
            "book_id": copy.book_id,
            "book_title": title,
            "owner_member_id": copy.owner_member_id,
            "owner_member_name": member_name,
            "status": copy.status,
            "format": copy.format,
            "condition": copy.condition,
            "location_display": display,
        }
        for copy, title, member_name in rows
    ]
    return {
        "cell_id": cell_id,
        "shelf_id": cell.shelf_id,
        "location_display": display,
        "items": items,
        "total": total,
    }


# ── 三类未定位清单（§5.1，互不混算） ──

def _copy_summary_rows(db: Session, where, *, limit: int, offset: int) -> tuple[list[dict], int]:
    base = select(BookCopy.id).where(*where)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        select(BookCopy, Book.title, Member.name)
        .join(Book, BookCopy.book_id == Book.id)
        .outerjoin(Member, BookCopy.owner_member_id == Member.id)
        .where(*where)
        .order_by(BookCopy.id)
        .limit(limit).offset(offset)
    ).all()
    items = [
        {
            "copy_id": copy.id,
            "book_id": copy.book_id,
            "book_title": title,
            "owner_member_id": copy.owner_member_id,
            "owner_member_name": member_name,
            "status": copy.status,
            "location": copy.location,
        }
        for copy, title, member_name in rows
    ]
    return items, total


def unlocated(db: Session, *, limit: int, offset: int) -> dict:
    """三类未定位：分别分页（共用 limit/offset），返回各自 items+total。"""
    unplaced = [
        BookCopy.copy_type == "physical",
        BookCopy.placement_shelf_id.is_(None),
    ]
    # 1. 有实体副本但位置未登记（无任何位置信息）
    items1, total1 = _copy_summary_rows(
        db, [*unplaced, or_(BookCopy.location.is_(None), BookCopy.location == "")],
        limit=limit, offset=offset)
    # 2. 仅有旧位置文字（有 location 文字但未结构化登记）
    items2, total2 = _copy_summary_rows(
        db, [*unplaced, BookCopy.location.is_not(None), BookCopy.location != ""],
        limit=limit, offset=offset)

    # 3. 书目尚无实体副本（可有电子副本；电子副本不进入实体占用统计）
    has_physical = (
        select(BookCopy.id)
        .where(BookCopy.book_id == Book.id, BookCopy.copy_type == "physical")
        .exists()
    )
    total3 = db.scalar(
        select(func.count()).select_from(Book).where(~has_physical)) or 0
    book_rows = db.scalars(
        select(Book).where(~has_physical).order_by(Book.id).limit(limit).offset(offset)
    ).all()
    page_book_ids = [b.id for b in book_rows]
    digital_counts: dict[int, int] = {}
    if page_book_ids:
        digital_counts = dict(db.execute(
            select(BookCopy.book_id, func.count())
            .where(BookCopy.book_id.in_(page_book_ids), BookCopy.copy_type == "digital")
            .group_by(BookCopy.book_id)
        ).all())
    items3 = [
        {
            "book_id": b.id,
            "book_title": b.title,
            "digital_copies": digital_counts.get(b.id, 0),
        }
        for b in book_rows
    ]

    return {
        "copies_without_placement": {"items": items1, "total": total1},
        "legacy_location_only": {"items": items2, "total": total2},
        "books_without_physical_copy": {"items": items3, "total": total3},
    }
