"""LOC-06：房间／书架／层格 CRUD、布局修改、归档与恢复接口回归。

覆盖验收标准（设计文档 LOC-06 行 + M0 冻结契约）：
- CRUD 全流程与分页清单；
- 稳定 ID：改名/排序不改变 room/shelf/layer/cell 的 ID；
- 归档/恢复：有副本引用的归档 409 LOCATION_IN_USE（含非在架状态）；
  祖先归档限制（归档祖先下写 409、恢复子对象前需先恢复祖先）；
- 版本乐观锁：期望版本失效 409 PLACEMENT_CHANGED；
- 幂等：同 key 同摘要重放原回执、同 key 不同摘要 409 IDEMPOTENCY_CONFLICT、
  回执查询区分 completed/not_found；
- 权限矩阵：Owner Web 写、Member Web 只读（写 403）、匿名 401、
  Agent Token 按 locations:read 读、Token 写 401；
- 审计：每个写操作生成对应 OperationLog（同事务）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, OperationLog
from tests.test_bug166_167_168_auth import _agent_token
from tests.test_storage_auth import _member_web_client


# ── 基建 ──

def _create_room(client: TestClient, *, key="room-1", code="R1", name="书房") -> dict:
    r = client.post("/api/v1/storage/rooms",
                    json={"idempotency_key": key, "code": code, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _create_shelf(client: TestClient, room_id: int, *, key="shelf-1", code="A01",
                  layers=None) -> dict:
    body = {"idempotency_key": key, "room_id": room_id, "code": code, "name": f"{code} 书架"}
    if layers is not None:
        body["layers"] = layers
    r = client.post("/api/v1/storage/shelves", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _place_copy(db_session: Session, *, cell_id: int, shelf_id: int,
                status: str = "in_shelf") -> int:
    book = Book(title=f"引用书-{cell_id}-{status}")
    db_session.add(book)
    db_session.flush()
    copy = BookCopy(book_id=book.id, copy_type="physical", status=status,
                    placement_shelf_id=shelf_id, placement_cell_id=cell_id)
    db_session.add(copy)
    db_session.commit()
    return copy.id


# ── CRUD 全流程与分页 ──

def test_room_shelf_crud_flow(client: TestClient) -> None:
    room = _create_room(client)
    assert room["code"] == "R1" and room["version"] == 1 and room["archived_at"] is None

    shelf = _create_shelf(client, room["id"], layers=[
        {"label": "上层", "cells": [{"label": "左"}, {"label": "右"}]},
        {"label": "下层"},  # 缺省 1 格
    ])
    assert shelf["room_id"] == room["id"]
    assert len(shelf["layers"]) == 2
    assert [len(l["cells"]) for l in shelf["layers"]] == [2, 1]
    codes = [c["code"] for l in shelf["layers"] for c in l["cells"]]
    assert codes == ["C1", "C2", "C3"]  # 自动编号架内唯一

    # 详情读取
    r = client.get(f"/api/v1/storage/shelves/{shelf['id']}")
    assert r.status_code == 200
    assert r.json()["data"]["id"] == shelf["id"]

    # 清单分页
    _create_room(client, key="room-2", code="R2", name="客厅")
    r = client.get("/api/v1/storage/rooms?limit=1&offset=0")
    data = r.json()["data"]
    assert data["total"] == 2 and len(data["items"]) == 1
    r = client.get("/api/v1/storage/rooms?limit=1&offset=1")
    assert len(r.json()["data"]["items"]) == 1
    r = client.get(f"/api/v1/storage/shelves?room_id={room['id']}")
    assert r.json()["data"]["total"] == 1
    r = client.get("/api/v1/storage/shelves?room_id=999999")
    assert r.json()["data"]["total"] == 0

    # 404
    r = client.get("/api/v1/storage/shelves/999999")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


def test_rename_and_reorder_keep_ids(client: TestClient) -> None:
    room = _create_room(client)
    shelf = _create_shelf(client, room["id"])
    layer_id = shelf["layers"][0]["id"]
    cell_id = shelf["layers"][0]["cells"][0]["id"]

    r = client.patch(f"/api/v1/storage/rooms/{room['id']}",
                     json={"version": 1, "name": "新书房", "sort_order": 5})
    assert r.status_code == 200, r.text
    renamed = r.json()["data"]
    assert renamed["id"] == room["id"] and renamed["name"] == "新书房"
    assert renamed["version"] == 2 and renamed["sort_order"] == 5

    r = client.patch(f"/api/v1/storage/shelves/{shelf['id']}",
                     json={"version": 1, "name": "新书架", "position_note": "靠窗"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["id"] == shelf["id"] and r.json()["data"]["version"] == 2

    r = client.put(f"/api/v1/storage/shelves/{shelf['id']}/layout", json={
        "idempotency_key": "layout-rename",
        "shelf_version": 2,
        "layers": [{"id": layer_id, "label": "改名层",
                    "cells": [{"id": cell_id, "label": "改名格", "sort_order": 3}]}],
    })
    assert r.status_code == 200, r.text
    layers = r.json()["data"]["layers"]
    assert layers[0]["id"] == layer_id and layers[0]["label"] == "改名层"
    assert layers[0]["cells"][0]["id"] == cell_id
    assert layers[0]["cells"][0]["label"] == "改名格"
    assert layers[0]["cells"][0]["sort_order"] == 3


def test_duplicate_code_conflict(client: TestClient) -> None:
    _create_room(client, key="room-a", code="RD")
    r = client.post("/api/v1/storage/rooms",
                    json={"idempotency_key": "room-b", "code": "RD", "name": "重号"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CODE_CONFLICT"


# ── 版本乐观锁 ──

def test_version_conflict_409(client: TestClient) -> None:
    room = _create_room(client)
    r = client.patch(f"/api/v1/storage/rooms/{room['id']}",
                     json={"version": 99, "name": "抢改"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"
    # 原数据未变
    r = client.get("/api/v1/storage/rooms")
    assert r.json()["data"]["items"][0]["name"] == "书房"

    shelf = _create_shelf(client, room["id"])
    r = client.put(f"/api/v1/storage/shelves/{shelf['id']}/layout", json={
        "idempotency_key": "layout-stale",
        "shelf_version": 99,
        "layers": [{"label": "新层"}],
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"


# ── 幂等 ──

def test_idempotent_replay_and_digest_conflict(client: TestClient) -> None:
    body = {"idempotency_key": "room-idem", "code": "RI", "name": "幂等房间"}
    r1 = client.post("/api/v1/storage/rooms", json=body)
    r2 = client.post("/api/v1/storage/rooms", json=body)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r2.json()["data"]["id"] == r1.json()["data"]["id"]
    r = client.get("/api/v1/storage/rooms")
    assert r.json()["data"]["total"] == 1  # 重放不新建

    r = client.post("/api/v1/storage/rooms",
                    json={"idempotency_key": "room-idem", "code": "RI2", "name": "换载荷"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    r = client.get("/api/v1/storage/rooms")
    assert r.json()["data"]["total"] == 1  # 冲突零写入

    # 回执查询：completed / not_found
    r = client.get("/api/v1/storage/operations/room-idem")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "completed"
    assert data["result"]["room"]["id"] == r1.json()["data"]["id"]
    r = client.get("/api/v1/storage/operations/never-seen")
    assert r.json()["data"]["status"] == "not_found"


# ── 归档 / 恢复 / 引用 ──

def test_archive_restore_room_and_shelf(client: TestClient) -> None:
    room = _create_room(client)
    shelf = _create_shelf(client, room["id"])

    # 归档书架 → 清单隐藏、详情可见
    r = client.patch(f"/api/v1/storage/shelves/{shelf['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["archived_at"] is not None
    assert client.get("/api/v1/storage/shelves").json()["data"]["total"] == 0
    r = client.get("/api/v1/storage/shelves?include_archived=true")
    assert r.json()["data"]["total"] == 1

    # 已归档书架的写被拒绝（仅可恢复）
    r = client.patch(f"/api/v1/storage/shelves/{shelf['id']}",
                     json={"version": 2, "name": "归档中改名"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_ARCHIVED"

    # 归档房间后：恢复书架须先恢复房间
    r = client.patch(f"/api/v1/storage/rooms/{room['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 200, r.text
    r = client.patch(f"/api/v1/storage/shelves/{shelf['id']}",
                     json={"version": 2, "archived": False})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_ARCHIVED"

    # 先恢复房间，再恢复书架
    r = client.patch(f"/api/v1/storage/rooms/{room['id']}",
                     json={"version": 2, "archived": False})
    assert r.status_code == 200
    assert r.json()["data"]["archived_at"] is None
    r = client.patch(f"/api/v1/storage/shelves/{shelf['id']}",
                     json={"version": 2, "archived": False})
    assert r.status_code == 200
    assert r.json()["data"]["archived_at"] is None
    assert client.get("/api/v1/storage/shelves").json()["data"]["total"] == 1


def test_archive_with_copy_reference_rejected(client: TestClient, db_session: Session) -> None:
    room = _create_room(client)
    shelf = _create_shelf(client, room["id"])
    layer = shelf["layers"][0]
    cell = layer["cells"][0]
    # 外借状态副本同样阻断归档（§7.1：即使外借或遗失也 409）
    _place_copy(db_session, cell_id=cell["id"], shelf_id=shelf["id"], status="lent_out")

    r = client.put(f"/api/v1/storage/shelves/{shelf['id']}/layout", json={
        "idempotency_key": "layout-archive-ref",
        "shelf_version": 1,
        "layers": [{"id": layer["id"], "cells": []}],  # 全量语义：省略格子=归档
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_IN_USE"

    r = client.patch(f"/api/v1/storage/shelves/{shelf['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_IN_USE"

    r = client.patch(f"/api/v1/storage/rooms/{room['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_IN_USE"


def test_layout_full_replace_and_invalid_relation(client: TestClient, db_session: Session) -> None:
    room = _create_room(client)
    shelf = _create_shelf(client, room["id"], key="shelf-layout", layers=[
        {"label": "第 1 层", "cells": [{"label": "左"}, {"label": "右"}]},
    ])
    shelf2 = _create_shelf(client, room["id"], key="shelf-other", code="B01")
    layer = shelf["layers"][0]
    cells = shelf["layers"][0]["cells"]

    # 格子挂到别的层 → 422 INVALID_RELATION
    room2 = _create_room(client, key="room-b", code="R2", name="客厅")
    other_shelf = client.get(f"/api/v1/storage/shelves/{shelf2['id']}").json()["data"]
    other_layer_id = other_shelf["layers"][0]["id"]
    r = client.put(f"/api/v1/storage/shelves/{shelf['id']}/layout", json={
        "idempotency_key": "layout-bad-relation",
        "shelf_version": 1,
        "layers": [{"id": layer["id"],
                    "cells": [{"id": cells[0]["id"]}, {"id": cells[1]["id"]}]},
                   {"id": other_layer_id}],
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_RELATION"

    # 全量替换：保留左格改名、归档右格、新建一格（编号不复用 → C3）
    r = client.put(f"/api/v1/storage/shelves/{shelf['id']}/layout", json={
        "idempotency_key": "layout-ok",
        "shelf_version": 1,
        "layers": [{"id": layer["id"],
                    "cells": [{"id": cells[0]["id"], "label": "保留格"},
                              {"label": "新增格"}]}],
    })
    assert r.status_code == 200, r.text
    new_layers = r.json()["data"]["layers"]
    assert len(new_layers) == 1
    active = new_layers[0]["cells"]
    assert [c["label"] for c in active] == ["保留格", "新增格"]
    assert active[0]["id"] == cells[0]["id"]  # 稳定 ID
    assert active[1]["code"] == "C3"  # C2 已归档不复用
    r = client.get(f"/api/v1/storage/shelves/{shelf['id']}?include_archived=true")
    all_cells = r.json()["data"]["layers"][0]["cells"]
    archived = [c for c in all_cells if c["archived_at"] is not None]
    assert len(archived) == 1 and archived[0]["id"] == cells[1]["id"]

    # 省略整个层 = 归档该层；层下有引用时 409（另起一个带引用书架验证）
    shelf3 = _create_shelf(client, room2["id"], key="shelf-ref", code="C01")
    cell3 = shelf3["layers"][0]["cells"][0]
    _place_copy(db_session, cell_id=cell3["id"], shelf_id=shelf3["id"])
    r = client.put(f"/api/v1/storage/shelves/{shelf3['id']}/layout", json={
        "idempotency_key": "layout-drop-layer",
        "shelf_version": 1,
        "layers": [],
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_IN_USE"


def test_layout_idempotent_replay(client: TestClient) -> None:
    room = _create_room(client)
    shelf = _create_shelf(client, room["id"])
    layer_id = shelf["layers"][0]["id"]
    cell_id = shelf["layers"][0]["cells"][0]["id"]
    body = {
        "idempotency_key": "layout-idem",
        "shelf_version": 1,
        "layers": [{"id": layer_id, "cells": [{"id": cell_id}, {"label": "新格"}]}],
    }
    r1 = client.put(f"/api/v1/storage/shelves/{shelf['id']}/layout", json=body)
    assert r1.status_code == 200, r1.text
    r2 = client.put(f"/api/v1/storage/shelves/{shelf['id']}/layout", json=body)
    assert r2.status_code == 200
    assert r2.json()["data"] == r1.json()["data"]  # 重放原回执
    # 实际只建了一个新格
    r = client.get(f"/api/v1/storage/shelves/{shelf['id']}")
    assert len(r.json()["data"]["layers"][0]["cells"]) == 2


# ── 权限矩阵 ──

def test_permission_matrix(client: TestClient, anon_client: TestClient,
                           db_session: Session) -> None:
    room = _create_room(client, key="perm-room", code="RP")

    # 匿名：读 401、写 401
    assert anon_client.get("/api/v1/storage/rooms").status_code == 401
    r = anon_client.post("/api/v1/storage/rooms",
                         json={"idempotency_key": "x", "code": "X", "name": "X"})
    assert r.status_code == 401

    # Member Web：读 200、写 403
    with _member_web_client(db_session) as member_client:
        assert member_client.get("/api/v1/storage/rooms").status_code == 200
        r = member_client.post("/api/v1/storage/rooms",
                               json={"idempotency_key": "m1", "code": "M1", "name": "M"},
                               headers={"Origin": "http://127.0.0.1"})
        assert r.status_code == 403
        r = member_client.patch(f"/api/v1/storage/rooms/{room['id']}",
                                json={"version": 1, "name": "越权"},
                                headers={"Origin": "http://127.0.0.1"})
        assert r.status_code == 403

    # Agent Token：有 locations:read 可读；无则 403；写（无 Cookie）401
    _, token = _agent_token(client, ["locations:read"])
    r = client.get("/api/v1/storage/rooms", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    _, no_scope = _agent_token(client, ["books:read"])
    r = client.get("/api/v1/storage/rooms", headers={"Authorization": f"Bearer {no_scope}"})
    assert r.status_code == 403
    # 纯 Token 主体（无 Web Cookie）写位置结构 → 401（结构化写仅 Owner Web）
    from app.main import app
    with TestClient(app) as token_only:
        r = token_only.post("/api/v1/storage/rooms",
                            json={"idempotency_key": "t1", "code": "T1", "name": "T"},
                            headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ── 审计 ──

def test_audit_records_created(client: TestClient, db_session: Session) -> None:
    room = _create_room(client, key="audit-room", code="RA")
    client.patch(f"/api/v1/storage/rooms/{room['id']}",
                 json={"version": 1, "name": "审计改名"})
    client.patch(f"/api/v1/storage/rooms/{room['id']}",
                 json={"version": 2, "archived": True})
    client.patch(f"/api/v1/storage/rooms/{room['id']}",
                 json={"version": 3, "archived": False})

    actions = db_session.scalars(
        select(OperationLog.action)
        .where(OperationLog.action.like("storage.room.%"))
        .order_by(OperationLog.id)
    ).all()
    assert actions == [
        "storage.room.create", "storage.room.update",
        "storage.room.archive", "storage.room.restore",
    ]
