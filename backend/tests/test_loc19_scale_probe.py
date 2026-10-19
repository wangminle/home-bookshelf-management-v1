"""LOC-19：设计 §12「性能验收建议」的合成规模探针（100 书架 / 2000 格 / 1 万副本）。

默认跳过（耗时）；设置 LOC19_SCALE=1 运行。仅断言**功能正确与分页上界**，
耗时只打印、不设阈值——设计明确要求"先记录实际耗时再定门槛，不提前承诺"。
同一探针可叠加 TEST_DATABASE_URL 在 PostgreSQL 上运行。

    LOC19_SCALE=1 python -m pytest backend/tests/test_loc19_scale_probe.py -q -s
"""
from __future__ import annotations

import os
import time

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.models import Book, BookCopy
from app.models.storage import ShelfCell, ShelfLayer, StorageRoom, StorageShelf
from app.services import storage_locations, storage_queries

pytestmark = pytest.mark.skipif(
    os.environ.get("LOC19_SCALE") != "1",
    reason="LOC-19：规模探针默认跳过，设置 LOC19_SCALE=1 运行（跳过不计为通过）",
)

SHELVES, LAYERS, CELLS_PER_LAYER = 100, 4, 5  # 100 × 4 × 5 = 2000 格
COPIES, PLACED, SHELF_ONLY = 10_000, 8_000, 1_000  # 其余 1000 册未登记


def _seed(db: Session) -> dict:
    room_id = db.execute(insert(StorageRoom).values(code="SCALE", name="规模房").returning(StorageRoom.id)).scalar_one()
    cell_ids: list[tuple[int, int]] = []
    shelf_ids: list[int] = []
    for s in range(SHELVES):
        shelf_id = db.execute(insert(StorageShelf).values(
            room_id=room_id, code=f"SC{s:03d}", name=f"架{s}").returning(StorageShelf.id)).scalar_one()
        shelf_ids.append(shelf_id)
        for layer in range(LAYERS):
            layer_id = db.execute(insert(ShelfLayer).values(
                shelf_id=shelf_id, label=f"L{layer}", sort_order=layer).returning(ShelfLayer.id)).scalar_one()
            for c in range(CELLS_PER_LAYER):
                cell_id = db.execute(insert(ShelfCell).values(
                    shelf_id=shelf_id, layer_id=layer_id, code=f"C{layer}{c}",
                    label=f"格{layer}{c}", sort_order=c).returning(ShelfCell.id)).scalar_one()
                cell_ids.append((shelf_id, cell_id))
    db.execute(insert(Book), [{"title": f"规模书{i}"} for i in range(COPIES)])
    book_ids = list(db.scalars(select(Book.id).order_by(Book.id)))
    rows = []
    for i, book_id in enumerate(book_ids):
        row = {"book_id": book_id, "copy_type": "physical", "status": "in_shelf"}
        if i < PLACED:
            shelf_id, cell_id = cell_ids[i % len(cell_ids)]
            row.update(placement_shelf_id=shelf_id, placement_cell_id=cell_id)
        elif i < PLACED + SHELF_ONLY:
            row.update(placement_shelf_id=shelf_ids[i % len(shelf_ids)])
        rows.append(row)
    db.execute(insert(BookCopy), rows)
    db.commit()
    return {"room_id": room_id, "cells": cell_ids}


def _timed(label: str, fn):
    start = time.perf_counter()
    result = fn()
    ms = (time.perf_counter() - start) * 1000
    print(f"[LOC19-SCALE] {label}: {ms:.1f} ms")
    return result


def test_scale_queries_are_paginated_and_correct(db_session: Session) -> None:
    dialect = db_session.bind.dialect.name
    print(f"[LOC19-SCALE] dialect={dialect} shelves={SHELVES} cells={SHELVES*LAYERS*CELLS_PER_LAYER} copies={COPIES}")
    world = _timed("seed", lambda: _seed(db_session))

    shelves = _timed("list_shelves(limit=50)", lambda: storage_locations.list_shelves(
        db_session, room_id=None, include_archived=False, limit=50, offset=0))
    assert shelves["total"] == SHELVES and len(shelves["items"]) == 50

    stats = _timed("rooms_stats", lambda: storage_queries.rooms_stats(db_session, [world["room_id"]]))
    assert stats[world["room_id"]]["assigned_copies"] == PLACED + SHELF_ONLY
    assert stats[world["room_id"]]["books_involved"] == PLACED + SHELF_ONLY  # 每册一本书，无重复

    shelf_id, cell_id = world["cells"][0]
    cell = _timed("list_cell_copies(limit=50)", lambda: storage_queries.list_cell_copies(
        db_session, cell_id, limit=50, offset=0))
    assert cell["total"] == PLACED // len(world["cells"]) and len(cell["items"]) <= 50

    unloc = _timed("unlocated(limit=50)", lambda: storage_queries.unlocated(db_session, limit=50, offset=0))
    unplaced = COPIES - PLACED - SHELF_ONLY
    assert unloc["copies_without_placement"]["total"] == unplaced
    assert len(unloc["copies_without_placement"]["items"]) == 50  # 分页上界
    assert unloc["legacy_location_only"]["total"] == 0
    assert unloc["books_without_physical_copy"]["total"] == 0
    assert db_session.scalar(select(func.count()).select_from(BookCopy)) == COPIES
