"""LOC-14：存量实体副本补录领域/API 回归。

覆盖验收标准（设计 LOC-14 行 + §7.2/§10.1/§12 + M0 契约修订 LOC-14）：
- 先查已有副本：补录入口消费未定位第③类（无实体副本书目），补录后该书目
  从第③类消失；已有副本优先分配位置，不重复建册；
- 新增册数只来自显式确认（new_copies.count），不按书目/照片数推定；
- 幂等：同 key 同载荷重放不重复创建（返回原回执，created id 相同），
  同 key 不同载荷 409 IDEMPOTENCY_CONFLICT；并发同 key 只建一次；
- 校验：书目/成员不存在 404，副本不属于该书目 404，电子副本 422，
  归档目标 409，批次形状（>100 册、重复 id、空条目）422；
- 原子性：任一条目失效整批零写入（含同批其他条目的新增副本）；
- 不改变 reading_progress / reading_notes / purchase_records；
- 审计 storage.copies.intake 同事务，payload 含路径快照；权限矩阵。
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app.models import (
    Book,
    BookCopy,
    Member,
    OperationLog,
    PurchaseRecord,
    ReadingNote,
    ReadingProgress,
)
from app.schemas.storage import IntakeCommitIn
from app.services import storage_intake
from tests.test_bug166_167_168_auth import _agent_token
from tests.test_storage_auth import _member_web_client
from tests.test_storage_crud import _create_room, _create_shelf

# ── 基建 ──


def _mk_book(db_session: Session, title: str) -> Book:
    book = Book(title=title)
    db_session.add(book)
    db_session.commit()
    return book


def _mk_copy(db_session: Session, book_id: int, *, copy_type: str = "physical",
             owner_member_id: int | None = None) -> BookCopy:
    copy = BookCopy(book_id=book_id, copy_type=copy_type,
                    owner_member_id=owner_member_id)
    db_session.add(copy)
    db_session.commit()
    return copy


def _mk_member(db_session: Session, name: str = "成员甲") -> Member:
    member = Member(name=name, role="member")
    db_session.add(member)
    db_session.commit()
    return member


def _world(client: TestClient) -> dict:
    room = _create_room(client)
    shelf_a = _create_shelf(client, room["id"], key="shelf-a", code="A01")
    shelf_b = _create_shelf(client, room["id"], key="shelf-b", code="B01")
    return {"room": room, "shelf_a": shelf_a, "shelf_b": shelf_b,
            "cell_a": shelf_a["layers"][0]["cells"][0],
            "cell_b": shelf_b["layers"][0]["cells"][0]}


def _intake(client: TestClient, *, key: str, items: list[dict]):
    return client.post("/api/v1/storage/copies",
                       json={"idempotency_key": key, "items": items})


def _copy_count(db_session: Session) -> int:
    return len(db_session.scalars(select(BookCopy)).all())


# ── 主流程 ──


def test_assign_existing_and_create_new(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book1 = _mk_book(db_session, "有两册旧副本")
    c1 = _mk_copy(db_session, book1.id)
    c2 = _mk_copy(db_session, book1.id)
    book2 = _mk_book(db_session, "无实体副本")
    _mk_copy(db_session, book2.id, copy_type="digital")  # 电子副本不算实体
    before = _copy_count(db_session)

    r = _intake(client, key="intake-1", items=[
        {"book_id": book1.id, "assign": [
            {"copy_id": c1.id, "placement_version": 1,
             "target": {"shelf_id": w["shelf_a"]["id"],
                        "cell_id": w["cell_a"]["id"]}},
            {"copy_id": c2.id, "placement_version": 1,
             "target": {"shelf_id": w["shelf_a"]["id"]}},  # 仅到书架
        ]},
        {"book_id": book2.id, "new_copies": {
            "count": 2, "owner_member_id": member.id,
            "target": {"shelf_id": w["shelf_b"]["id"],
                       "cell_id": w["cell_b"]["id"]}}},
    ])
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["assigned"] == 2 and data["created"] == 2
    item2 = next(i for i in data["items"] if i["book_id"] == book2.id)
    assert len(item2["created"]) == 2

    db_session.expire_all()
    assert _copy_count(db_session) == before + 2  # 只增不减
    row1 = db_session.get(BookCopy, c1.id)
    assert row1.placement_cell_id == w["cell_a"]["id"]
    assert row1.placement_version == 2
    row2 = db_session.get(BookCopy, c2.id)
    assert row2.placement_shelf_id == w["shelf_a"]["id"]
    assert row2.placement_cell_id is None
    for new_id in item2["created"]:
        row = db_session.get(BookCopy, new_id)
        assert row.book_id == book2.id and row.copy_type == "physical"
        assert row.owner_member_id == member.id
        assert row.status == "in_shelf"
        assert row.placement_cell_id == w["cell_b"]["id"]
        assert row.placement_version == 1

    # 审计：单条批量记录，payload 含路径快照
    import json as _json
    log = db_session.scalars(select(OperationLog).where(
        OperationLog.action == "storage.copies.intake")).one()
    payload = _json.loads(log.payload)
    assert payload["items"][0]["assigned"][0]["to"]["display"].startswith(
        f"{w['room']['name']} · A01 书架")

    # 回执
    r = client.get("/api/v1/storage/operations/intake-1")
    assert r.json()["data"]["status"] == "completed"


def test_unlocated_bucket3_consumed(client: TestClient, db_session: Session) -> None:
    """补录入口消费未定位第③类：无实体副本书目在补录后从第③类消失。"""
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "待补录书目")
    bucket = client.get("/api/v1/storage/unlocated").json()["data"]
    assert bucket["books_without_physical_copy"]["total"] == 1

    r = _intake(client, key="intake-b3", items=[{"book_id": book.id, "new_copies": {
        "count": 1, "owner_member_id": member.id,
        "target": {"shelf_id": w["shelf_a"]["id"]}}}])
    assert r.status_code == 201
    bucket = client.get("/api/v1/storage/unlocated").json()["data"]
    assert bucket["books_without_physical_copy"]["total"] == 0


# ── 幂等 ──


def test_replay_no_duplicate_create(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "幂等书目")
    before = _copy_count(db_session)
    items = [{"book_id": book.id, "new_copies": {
        "count": 2, "owner_member_id": member.id,
        "target": {"shelf_id": w["shelf_a"]["id"]}}}]

    r1 = _intake(client, key="intake-idem", items=items)
    r2 = _intake(client, key="intake-idem", items=items)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r2.json()["data"] == r1.json()["data"]  # 重放原回执（含原 created id）
    db_session.expire_all()
    assert _copy_count(db_session) == before + 2  # 不重复创建

    # 同 key 不同载荷 → 409
    r = _intake(client, key="intake-idem", items=[{"book_id": book.id, "new_copies": {
        "count": 1, "owner_member_id": member.id,
        "target": {"shelf_id": w["shelf_a"]["id"]}}}])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert _copy_count(db_session) == before + 2


def test_concurrent_same_key_creates_once(client: TestClient,
                                          db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "并发同 key")
    before = _copy_count(db_session)
    owner_id = db_session.scalars(select(Member.id).where(Member.role == "owner")).first()
    payload = IntakeCommitIn(idempotency_key="intake-race", items=[{
        "book_id": book.id,
        "new_copies": {"count": 2, "owner_member_id": member.id,
                       "target": {"shelf_id": w["shelf_a"]["id"], "cell_id": None}},
    }])
    results: list[dict] = []
    errors: list[BaseException] = []

    def worker() -> None:
        session = db_module.SessionLocal()
        try:
            results.append(storage_intake.commit_intake(
                session, payload, operator_member_id=owner_id))
        except Exception as exc:  # noqa: BLE001 - 汇聚到主线程断言
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    assert len(results) == 4
    assert all(r == results[0] for r in results)  # 其余均为重放回执
    db_session.expire_all()
    assert _copy_count(db_session) == before + 2  # 只建一次


# ── 校验与原子性 ──


def test_validation_errors(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "校验书目")
    other_book = _mk_book(db_session, "他书")
    copy = _mk_copy(db_session, book.id)
    digital = _mk_copy(db_session, book.id, copy_type="digital")
    target = {"shelf_id": w["shelf_a"]["id"]}

    # 书目不存在 → 404
    r = _intake(client, key="v-book", items=[{"book_id": 999999, "new_copies": {
        "count": 1, "owner_member_id": member.id, "target": target}}])
    assert r.status_code == 404
    # 成员不存在 → 404
    r = _intake(client, key="v-member", items=[{"book_id": book.id, "new_copies": {
        "count": 1, "owner_member_id": 999999, "target": target}}])
    assert r.status_code == 404
    # 副本不属于该书目 → 404
    r = _intake(client, key="v-copy", items=[{"book_id": other_book.id, "assign": [
        {"copy_id": copy.id, "placement_version": 1, "target": target}]}])
    assert r.status_code == 404
    # 电子副本分配实体位置 → 422 INVALID_COPY_TYPE
    r = _intake(client, key="v-digital", items=[{"book_id": book.id, "assign": [
        {"copy_id": digital.id, "placement_version": 1, "target": target}]}])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_COPY_TYPE"
    # 空条目 → 422（schema）
    r = _intake(client, key="v-empty", items=[{"book_id": book.id}])
    assert r.status_code == 422
    # 重复 book_id → 422 INVALID_BATCH
    r = _intake(client, key="v-dupbook", items=[
        {"book_id": book.id, "assign": [
            {"copy_id": copy.id, "placement_version": 1, "target": target}]},
        {"book_id": book.id, "new_copies": {
            "count": 1, "owner_member_id": member.id, "target": target}}])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_BATCH"
    # 全部拒绝零写入
    db_session.expire_all()
    assert db_session.get(BookCopy, copy.id).placement_shelf_id is None


def test_batch_limit_100(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "上限书目")
    r = _intake(client, key="v-limit", items=[{"book_id": book.id, "new_copies": {
        "count": 101, "owner_member_id": member.id,
        "target": {"shelf_id": w["shelf_a"]["id"]}}}])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_BATCH"
    assert _copy_count(db_session) == 0


def test_archived_target_rejected(client: TestClient, db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "归档目标")
    r = client.patch(f"/api/v1/storage/shelves/{w['shelf_a']['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 200
    r = _intake(client, key="v-archived", items=[{"book_id": book.id, "new_copies": {
        "count": 1, "owner_member_id": member.id,
        "target": {"shelf_id": w["shelf_a"]["id"]}}}])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_ARCHIVED"
    assert _copy_count(db_session) == 0


def test_atomic_reject_on_stale_version(client: TestClient,
                                        db_session: Session) -> None:
    """任一条目失效整批零写入：含同批其他条目的新增副本也不落库。"""
    w = _world(client)
    member = _mk_member(db_session)
    book1 = _mk_book(db_session, "版本失效书")
    copy = _mk_copy(db_session, book1.id)
    book2 = _mk_book(db_session, "同批新书目")
    before = _copy_count(db_session)

    r = _intake(client, key="v-atomic", items=[
        {"book_id": book1.id, "assign": [
            {"copy_id": copy.id, "placement_version": 99,  # 过期版本
             "target": {"shelf_id": w["shelf_a"]["id"]}}]},
        {"book_id": book2.id, "new_copies": {
            "count": 1, "owner_member_id": member.id,
            "target": {"shelf_id": w["shelf_b"]["id"]}}},
    ])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"
    db_session.rollback()  # 共享会话夹具：丢弃被拒事务的 flush 残渣
    db_session.expire_all()
    assert _copy_count(db_session) == before  # 新增副本未落库
    assert db_session.get(BookCopy, copy.id).placement_shelf_id is None
    r = client.get("/api/v1/storage/operations/v-atomic")
    assert r.json()["data"]["status"] == "not_found"  # 回执未登记


# ── 不改进度/笔记/购买 ──


def test_does_not_touch_progress_notes_purchases(client: TestClient,
                                                 db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "三表不动")
    copy = _mk_copy(db_session, book.id)
    progress = ReadingProgress(book_id=book.id, member_id=member.id,
                               status="reading", current_page=42)
    note = ReadingNote(book_id=book.id, member_id=member.id,
                       note_type="excerpt", content_md="原文摘录")
    purchase = PurchaseRecord(book_id=book.id, copy_id=copy.id, price=59.0)
    db_session.add_all([progress, note, purchase])
    db_session.commit()

    def snapshot():
        db_session.expire_all()
        p = db_session.get(ReadingProgress, progress.id)
        n = db_session.get(ReadingNote, note.id)
        pr = db_session.get(PurchaseRecord, purchase.id)
        return ((p.status, p.current_page), (n.note_type, n.content_md),
                (pr.price, pr.copy_id))

    before = snapshot()
    r = _intake(client, key="intake-safe", items=[{"book_id": book.id, "assign": [
        {"copy_id": copy.id, "placement_version": 1,
         "target": {"shelf_id": w["shelf_a"]["id"],
                    "cell_id": w["cell_a"]["id"]}}]}])
    assert r.status_code == 201
    assert snapshot() == before
    # 三张表行数不变
    assert len(db_session.scalars(select(ReadingProgress)).all()) == 1
    assert len(db_session.scalars(select(ReadingNote)).all()) == 1
    assert len(db_session.scalars(select(PurchaseRecord)).all()) == 1


# ── 权限矩阵 ──


def test_permission_matrix(client: TestClient, anon_client: TestClient,
                           db_session: Session) -> None:
    w = _world(client)
    member = _mk_member(db_session)
    book = _mk_book(db_session, "权限书目")
    body = {"idempotency_key": "perm-intake", "items": [
        {"book_id": book.id, "new_copies": {
            "count": 1, "owner_member_id": member.id,
            "target": {"shelf_id": w["shelf_a"]["id"]}}}]}

    assert anon_client.post("/api/v1/storage/copies", json=body).status_code == 401
    with _member_web_client(db_session) as member_client:
        r = member_client.post("/api/v1/storage/copies", json=body,
                               headers={"Origin": "http://127.0.0.1"})
        assert r.status_code == 403
    _, token = _agent_token(client, ["locations:read", "books:write"])
    from app.main import app
    with TestClient(app) as token_only:
        r = token_only.post("/api/v1/storage/copies", json=body,
                            headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
    db_session.expire_all()
    assert _copy_count(db_session) == 0  # 越权尝试零写入
