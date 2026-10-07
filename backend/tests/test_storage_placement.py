"""LOC-13：单册位置与批量移动领域/API 回归。

覆盖验收标准（设计 LOC-13 行 + §7.2/§7.3/§9 + M0 契约修订 LOC-13）：
- 单册：分配到格子/仅到书架/移动/清除（保留与清空旧 location 文字两分支，
  缺省 keep_legacy_location 422）；期望版本 409；归档目标 409；非法层格 422；
  电子副本 422；改路径操作他书副本 404；旧客户端写 location 409；幂等重放；
- 批量：预览 dry-run 不落库（含每册源→目标与将发生的版本变化、preview_digest）；
  执行原子提交（任一版本失效整批零写入）；>100 册 422；摘要被篡改 409；
  幂等重放；移动不新增副本、不改副本状态；
- 并发：移动与归档并发不产生无效新引用（锁序保证）；
- 权限矩阵与审计（storage.placement.* 同事务，payload 含路径快照）。
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app.models import Book, BookCopy, Member, OperationLog, StorageShelf
from app.models.storage import LocationOperation
from app.schemas.storage import PlacementTarget, PlacementUpdate, ShelfUpdate
from app.services import storage_locations, storage_placement
from app.services.storage_tx import LocationInUse, PlacementChanged
from tests.test_bug166_167_168_auth import _agent_token
from tests.test_storage_auth import _member_web_client
from tests.test_storage_crud import _create_room, _create_shelf

# ── 基建 ──


def _mk_copy(db_session: Session, *, title: str | None = None,
             location: str | None = None, copy_type: str = "physical",
             status: str = "in_shelf") -> tuple[Book, BookCopy]:
    book = Book(title=title or f"书-{Book.__name__}-{location}-{copy_type}-{status}")
    db_session.add(book)
    db_session.flush()
    copy = BookCopy(book_id=book.id, copy_type=copy_type,
                    location=location, status=status)
    db_session.add(copy)
    db_session.commit()
    return book, copy


def _placement_url(book_id: int, copy_id: int) -> str:
    return f"/api/v1/books/{book_id}/copies/{copy_id}/placement"


def _world(client: TestClient) -> dict:
    """两房间两书架：A（2 层 2 格）与 B。"""
    room = _create_room(client)
    shelf_a = _create_shelf(client, room["id"], key="shelf-a", code="A01", layers=[
        {"label": "上层", "cells": [{"label": "左"}, {"label": "右"}]},
    ])
    shelf_b = _create_shelf(client, room["id"], key="shelf-b", code="B01")
    return {"room": room, "shelf_a": shelf_a, "shelf_b": shelf_b,
            "cell_a": shelf_a["layers"][0]["cells"][0],
            "cell_b": shelf_b["layers"][0]["cells"][0]}


def _preview(client: TestClient, target: dict, copy_ids: list[int]):
    return client.post("/api/v1/storage/placements/preview", json={
        "target": target, "copies": [{"copy_id": c} for c in copy_ids]})


def _execute_from_preview(client: TestClient, preview: dict, *, key: str):
    return client.post("/api/v1/storage/placements", json={
        "idempotency_key": key,
        "preview_digest": preview["preview_digest"],
        "target": {k: preview["target"][k]
                   for k in ("shelf_id", "cell_id", "shelf_version", "cell_version")},
        "copies": [{"copy_id": c["copy_id"],
                    "placement_version": c["placement_version"]}
                   for c in preview["copies"]],
    })


# ── 单册：分配 / 移动 / 清除 ──


def test_set_move_and_clear_keep_legacy(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session, location="旧书柜", status="lent_out")
    url = _placement_url(book.id, copy.id)

    # 分配到格子
    r = client.patch(url, json={"placement_version": 1, "target": {
        "shelf_id": w["shelf_a"]["id"], "cell_id": w["cell_a"]["id"]}})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["placement"] == {"shelf_id": w["shelf_a"]["id"],
                                 "cell_id": w["cell_a"]["id"], "version": 2}
    assert data["location_display"].startswith(f"{w['room']['name']} · A01 书架")
    assert data["location"] == "旧书柜"  # 旧文字保留
    assert data["status"] == "lent_out"  # 移动不改状态

    # 移动到仅书架
    r = client.patch(url, json={"placement_version": 2,
                                "target": {"shelf_id": w["shelf_b"]["id"]}})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["placement"]["cell_id"] is None
    assert data["placement"]["version"] == 3
    assert data["location_display"].endswith("格子待登记")

    # 清除并保留旧文字
    r = client.patch(url, json={"placement_version": 3, "target": None,
                                "keep_legacy_location": True})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["placement"] is None
    assert data["location"] == "旧书柜"
    assert data["location_display"] == "旧书柜"  # 无结构化定位时回退旧文字


def test_clear_drop_legacy(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session, location="旧书柜")
    url = _placement_url(book.id, copy.id)
    client.patch(url, json={"placement_version": 1,
                            "target": {"shelf_id": w["shelf_a"]["id"]}})
    r = client.patch(url, json={"placement_version": 2, "target": None,
                                "keep_legacy_location": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["placement"] is None
    assert data["location"] is None
    assert data["location_display"] is None


@pytest.mark.parametrize("keep_legacy", [True, False])
def test_clear_refresh_and_set_again(client: TestClient, db_session: Session,
                                    keep_legacy: bool) -> None:
    """BUG-298：用刷新详情返回的真实版本重设位置，不手工补版本。"""
    w = _world(client)
    book, copy = _mk_copy(db_session, location="旧书柜")
    url = _placement_url(book.id, copy.id)
    response = client.patch(url, json={"placement_version": 1,
                                     "target": {"shelf_id": w["shelf_a"]["id"]}})
    assert response.status_code == 200, response.text
    response = client.patch(url, json={"placement_version": 2, "target": None,
                                     "keep_legacy_location": keep_legacy})
    assert response.status_code == 200, response.text
    cleared = response.json()["data"]
    assert cleared["placement"] is None
    assert cleared["placement_version"] == 3

    response = client.get(f"/api/v1/books/{book.id}")
    assert response.status_code == 200, response.text
    refreshed = next(c for c in response.json()["data"]["copies"] if c["id"] == copy.id)
    assert refreshed["placement"] is None
    assert refreshed["placement_version"] == cleared["placement_version"]
    assert refreshed["location_display"] == ("旧书柜" if keep_legacy else None)

    response = client.patch(url, json={
        "placement_version": refreshed["placement_version"],
        "target": {"shelf_id": w["shelf_b"]["id"], "cell_id": w["cell_b"]["id"]},
    })
    assert response.status_code == 200, response.text
    reset = response.json()["data"]
    assert reset["placement_version"] == 4
    assert reset["placement"] == {"shelf_id": w["shelf_b"]["id"],
                                  "cell_id": w["cell_b"]["id"], "version": 4}


def test_clear_requires_explicit_flag(client: TestClient, db_session: Session) -> None:
    book, copy = _mk_copy(db_session)
    url = _placement_url(book.id, copy.id)
    # 清除缺省 keep_legacy_location → 422
    r = client.patch(url, json={"placement_version": 1, "target": None})
    assert r.status_code == 422
    # 设置时携带 keep_legacy_location → 422
    r = client.patch(url, json={"placement_version": 1,
                                "target": {"shelf_id": 1},
                                "keep_legacy_location": True})
    assert r.status_code == 422


def test_version_conflict(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session)
    r = client.patch(_placement_url(book.id, copy.id),
                     json={"placement_version": 99,
                           "target": {"shelf_id": w["shelf_a"]["id"]}})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"
    db_session.expire_all()
    assert db_session.get(BookCopy, copy.id).placement_shelf_id is None


def test_archived_target_rejected(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session)
    r = client.patch(f"/api/v1/storage/shelves/{w['shelf_a']['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 200
    r = client.patch(_placement_url(book.id, copy.id),
                     json={"placement_version": 1,
                           "target": {"shelf_id": w["shelf_a"]["id"]}})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_ARCHIVED"

    # 上级房间归档同样拒绝
    r = client.patch(f"/api/v1/storage/rooms/{w['room']['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 200
    r = client.patch(_placement_url(book.id, copy.id),
                     json={"placement_version": 1,
                           "target": {"shelf_id": w["shelf_b"]["id"]}})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_ARCHIVED"


def test_invalid_relation_404_and_copy_type(client: TestClient,
                                            db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session)
    other_book, other_copy = _mk_copy(db_session)

    # 格子不属于目标书架 → 422 INVALID_RELATION
    r = client.patch(_placement_url(book.id, copy.id),
                     json={"placement_version": 1, "target": {
                         "shelf_id": w["shelf_b"]["id"],
                         "cell_id": w["cell_a"]["id"]}})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_RELATION"

    # 目标书架不存在 → 404
    r = client.patch(_placement_url(book.id, copy.id),
                     json={"placement_version": 1, "target": {"shelf_id": 999999}})
    assert r.status_code == 404

    # 改路径操作他书副本 → 404
    r = client.patch(_placement_url(book.id, other_copy.id),
                     json={"placement_version": 1,
                           "target": {"shelf_id": w["shelf_a"]["id"]}})
    assert r.status_code == 404
    db_session.expire_all()
    assert db_session.get(BookCopy, other_copy.id).placement_shelf_id is None

    # 电子副本 → 422 INVALID_COPY_TYPE
    _, digital = _mk_copy(db_session, copy_type="digital")
    r = client.patch(_placement_url(digital.book_id, digital.id),
                     json={"placement_version": 1,
                           "target": {"shelf_id": w["shelf_a"]["id"]}})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_COPY_TYPE"


def test_legacy_location_write_409(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session)
    url = _placement_url(book.id, copy.id)
    client.patch(url, json={"placement_version": 1,
                            "target": {"shelf_id": w["shelf_a"]["id"]}})
    # 已结构化定位副本携带 location 字段 → 409 引导
    r = client.patch(url, json={"placement_version": 2,
                                "target": {"shelf_id": w["shelf_b"]["id"]},
                                "location": "旧文字"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LEGACY_LOCATION_WRITE"
    # 未定位副本携带 location 字段同样拒绝（本端点不写旧文字）
    _, plain = _mk_copy(db_session)
    r = client.patch(_placement_url(plain.book_id, plain.id),
                     json={"placement_version": 1,
                           "target": {"shelf_id": w["shelf_a"]["id"]},
                           "location": "旧文字"})
    assert r.status_code == 409


def test_single_idempotent_replay(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session)
    body = {"idempotency_key": "set-idem", "placement_version": 1,
            "target": {"shelf_id": w["shelf_a"]["id"],
                       "cell_id": w["cell_a"]["id"]}}
    r1 = client.patch(_placement_url(book.id, copy.id), json=body)
    r2 = client.patch(_placement_url(book.id, copy.id), json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json()["data"] == r1.json()["data"]
    db_session.expire_all()
    assert db_session.get(BookCopy, copy.id).placement_version == 2  # 只 bump 一次

    # 回执查询
    r = client.get("/api/v1/storage/operations/set-idem")
    assert r.json()["data"]["status"] == "completed"


# ── 批量移动 ──


def test_batch_preview_dry_run(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    _, c1 = _mk_copy(db_session, title="已上架")
    _, c2 = _mk_copy(db_session, title="旧文字", location="阁楼")
    _, c3 = _mk_copy(db_session, title="无位置")
    client.patch(_placement_url(c1.book_id, c1.id),
                 json={"placement_version": 1, "target": {
                     "shelf_id": w["shelf_a"]["id"], "cell_id": w["cell_a"]["id"]}})

    ops_before = len(db_session.scalars(select(LocationOperation)).all())
    r = _preview(client, {"shelf_id": w["shelf_b"]["id"],
                          "cell_id": w["cell_b"]["id"]},
                 [c3.id, c1.id, c2.id])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["target"]["shelf_version"] == w["shelf_b"]["version"]
    assert data["preview_digest"]
    items = data["copies"]
    assert [i["copy_id"] for i in items] == sorted([c1.id, c2.id, c3.id])
    by_id = {i["copy_id"]: i for i in items}
    assert by_id[c1.id]["from"]["display"].startswith(
        f"{w['room']['name']} · A01 书架")
    assert by_id[c1.id]["placement_version"] == 2
    assert by_id[c1.id]["new_placement_version"] == 3
    assert by_id[c2.id]["from"]["shelf_id"] is None
    assert by_id[c1.id]["to"]["display"] == data["target"]["display"]

    # dry-run 不落库：版本不变、操作登记数不增（房间/书架创建本身会登记）
    db_session.expire_all()
    assert db_session.get(BookCopy, c1.id).placement_version == 2
    assert db_session.get(BookCopy, c1.id).placement_cell_id == w["cell_a"]["id"]
    assert len(db_session.scalars(select(LocationOperation)).all()) == ops_before


def test_batch_execute_and_receipt(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    copies = [_mk_copy(db_session, title=f"批-{i}")[1] for i in range(3)]
    copies[0].status = "lent_out"  # 移动不改状态
    db_session.commit()
    before_count = len(db_session.scalars(select(BookCopy)).all())

    preview = _preview(client, {"shelf_id": w["shelf_b"]["id"],
                                "cell_id": w["cell_b"]["id"]},
                       [c.id for c in copies]).json()["data"]
    r = _execute_from_preview(client, preview, key="batch-1")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["moved"] == 3
    assert [c["placement_version"] for c in data["copies"]] == [2, 2, 2]

    db_session.expire_all()
    for c in copies:
        row = db_session.get(BookCopy, c.id)
        assert row.placement_shelf_id == w["shelf_b"]["id"]
        assert row.placement_cell_id == w["cell_b"]["id"]
        assert row.placement_version == 2
    assert db_session.get(BookCopy, copies[0].id).status == "lent_out"
    assert len(db_session.scalars(select(BookCopy)).all()) == before_count  # 不新增副本

    # 审计：同事务单条批量记录，payload 含原/新位置 ID 与路径快照
    log = db_session.scalars(select(OperationLog).where(
        OperationLog.action == "storage.placement.move")).one()
    import json as _json
    payload = _json.loads(log.payload)
    assert len(payload["copies"]) == 3
    assert payload["copies"][0]["to"]["display"].startswith(
        f"{w['room']['name']} · B01 书架")

    # 回执
    r = client.get("/api/v1/storage/operations/batch-1")
    assert r.json()["data"]["status"] == "completed"
    assert r.json()["data"]["result"]["moved"] == 3


def test_batch_limit_100(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    book = Book(title="批量上限")
    db_session.add(book)
    db_session.flush()
    ids = []
    for _ in range(101):
        c = BookCopy(book_id=book.id, copy_type="physical")
        db_session.add(c)
        db_session.flush()
        ids.append(c.id)
    db_session.commit()

    r = _preview(client, {"shelf_id": w["shelf_a"]["id"]}, ids)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_BATCH"
    r = client.post("/api/v1/storage/placements", json={
        "idempotency_key": "batch-over", "preview_digest": "x",
        "target": {"shelf_id": w["shelf_a"]["id"], "cell_id": None,
                   "shelf_version": 1, "cell_version": None},
        "copies": [{"copy_id": i, "placement_version": 1} for i in ids]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_BATCH"

    # 批次内 copy_id 重复 → 422
    r = _preview(client, {"shelf_id": w["shelf_a"]["id"]}, [ids[0], ids[0]])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_BATCH"


def test_batch_atomic_reject_on_stale_version(client: TestClient,
                                              db_session: Session) -> None:
    w = _world(client)
    _, c1 = _mk_copy(db_session, title="原子-1")
    _, c2 = _mk_copy(db_session, title="原子-2")
    preview = _preview(client, {"shelf_id": w["shelf_b"]["id"]},
                       [c1.id, c2.id]).json()["data"]
    # 预览后 c1 被单独移动（版本 +1），批量执行整批拒绝
    r = client.patch(_placement_url(c1.book_id, c1.id),
                     json={"placement_version": 1,
                           "target": {"shelf_id": w["shelf_a"]["id"]}})
    assert r.status_code == 200
    r = _execute_from_preview(client, preview, key="batch-stale")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"

    # 零写入：c2 未动、c1 停在单独移动后的位置、幂等 key 未登记。
    # 共享会话夹具下，被拒事务的 flush 残渣仍在会话内（未提交），先 rollback
    # 再看已提交状态（生产每请求独立会话，关闭即回滚）。
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(BookCopy, c2.id).placement_shelf_id is None
    assert db_session.get(BookCopy, c1.id).placement_shelf_id == w["shelf_a"]["id"]
    r = client.get("/api/v1/storage/operations/batch-stale")
    assert r.json()["data"]["status"] == "not_found"


def test_batch_digest_mismatch(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    _, c1 = _mk_copy(db_session)
    preview = _preview(client, {"shelf_id": w["shelf_b"]["id"]}, [c1.id]).json()["data"]

    # 篡改摘要
    r = client.post("/api/v1/storage/placements", json={
        "idempotency_key": "batch-tampered", "preview_digest": "0" * 64,
        "target": {"shelf_id": w["shelf_b"]["id"], "cell_id": None,
                   "shelf_version": 1, "cell_version": None},
        "copies": [{"copy_id": c1.id, "placement_version": 1}]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"

    # 摘要有效但请求体副本清单被改（换了目标书架，摘要仍是旧目标的）→ 409
    r = client.post("/api/v1/storage/placements", json={
        "idempotency_key": "batch-tampered2",
        "preview_digest": preview["preview_digest"],
        "target": {"shelf_id": w["shelf_a"]["id"], "cell_id": None,
                   "shelf_version": 1, "cell_version": None},
        "copies": [{"copy_id": c1.id, "placement_version": 1}]})
    assert r.status_code == 409
    db_session.expire_all()
    assert db_session.get(BookCopy, c1.id).placement_shelf_id is None


def test_batch_idempotent_replay(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    _, c1 = _mk_copy(db_session)
    preview = _preview(client, {"shelf_id": w["shelf_b"]["id"]}, [c1.id]).json()["data"]
    r1 = _execute_from_preview(client, preview, key="batch-idem")
    r2 = _execute_from_preview(client, preview, key="batch-idem")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json()["data"] == r1.json()["data"]
    db_session.expire_all()
    assert db_session.get(BookCopy, c1.id).placement_version == 2  # 只 bump 一次


# ── 并发：移动与归档 ──


def test_concurrent_move_and_archive(client: TestClient, db_session: Session) -> None:
    """移动（源 A → 目标 B）与归档 A 并发：无论锁序先后，最终不得出现
    「副本引用已归档书架」的无效新引用。"""
    w = _world(client)
    book, copy = _mk_copy(db_session)
    client.patch(_placement_url(book.id, copy.id),
                 json={"placement_version": 1, "target": {
                     "shelf_id": w["shelf_a"]["id"], "cell_id": w["cell_a"]["id"]}})
    owner_id = db_session.scalars(select(Member.id).where(Member.role == "owner")).first()
    errors: list[BaseException | str] = []

    def move_worker() -> None:
        session = db_module.SessionLocal()
        try:
            for _ in range(30):
                ver = session.get(BookCopy, copy.id).placement_version
                try:
                    storage_placement.set_placement(
                        session, book.id, copy.id,
                        PlacementUpdate(placement_version=ver, target=PlacementTarget(
                            shelf_id=w["shelf_b"]["id"],
                            cell_id=w["cell_b"]["id"])),
                        operator_member_id=owner_id)
                    return
                except PlacementChanged:
                    session.rollback()
                    continue
            errors.append("move retry exhausted")
        except Exception as exc:  # noqa: BLE001 - 汇聚到主线程断言
            errors.append(exc)
        finally:
            session.close()

    def archive_worker() -> None:
        session = db_module.SessionLocal()
        try:
            for _ in range(30):
                ver = session.get(StorageShelf, w["shelf_a"]["id"]).version
                try:
                    storage_locations.update_shelf(
                        session, w["shelf_a"]["id"],
                        ShelfUpdate(version=ver, archived=True),
                        operator_member_id=owner_id)
                    return
                except PlacementChanged:
                    session.rollback()
                    continue
                except LocationInUse:
                    return  # 移动先胜出：副本仍引用 A，归档合法被拒
            errors.append("archive retry exhausted")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=move_worker),
               threading.Thread(target=archive_worker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors

    db_session.expire_all()
    final_copy = db_session.get(BookCopy, copy.id)
    final_shelf_a = db_session.get(StorageShelf, w["shelf_a"]["id"])
    inconsistent = (final_copy.placement_shelf_id == w["shelf_a"]["id"]
                    and final_shelf_a.archived_at is not None)
    assert not inconsistent, "副本停留在已归档书架 = 无效引用"


# ── 权限矩阵 ──


def test_permission_matrix(client: TestClient, anon_client: TestClient,
                           db_session: Session) -> None:
    w = _world(client)
    book, copy = _mk_copy(db_session)
    patch_body = {"placement_version": 1, "target": {"shelf_id": w["shelf_a"]["id"]}}
    preview_body = {"target": {"shelf_id": w["shelf_a"]["id"]},
                    "copies": [{"copy_id": copy.id}]}

    # 匿名 401
    assert anon_client.patch(_placement_url(book.id, copy.id),
                             json=patch_body).status_code == 401
    assert anon_client.post("/api/v1/storage/placements/preview",
                            json=preview_body).status_code == 401
    assert anon_client.post("/api/v1/storage/placements", json={}).status_code == 401

    # Member Web 403
    with _member_web_client(db_session) as member_client:
        h = {"Origin": "http://127.0.0.1"}
        assert member_client.patch(_placement_url(book.id, copy.id),
                                   json=patch_body, headers=h).status_code == 403
        assert member_client.post("/api/v1/storage/placements/preview",
                                  json=preview_body, headers=h).status_code == 403

    # Agent Token（无 Cookie）401
    _, token = _agent_token(client, ["locations:read", "books:write"])
    from app.main import app
    with TestClient(app) as token_only:
        h = {"Authorization": f"Bearer {token}"}
        assert token_only.patch(_placement_url(book.id, copy.id),
                                json=patch_body, headers=h).status_code == 401
        assert token_only.post("/api/v1/storage/placements/preview",
                               json=preview_body, headers=h).status_code == 401
