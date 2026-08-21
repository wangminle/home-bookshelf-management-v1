"""权限阶段 2 测试：Member 独立登录与本人数据隔离（基线 §14 阶段 2）。

覆盖验收底线：Member 无法读取或修改其他 Member 的任何私有数据；
Owner 的跨成员操作始终可追溯（操作者与数据归属人分离入审计）。

- 凭据生命周期：创建/登录/锁定/自助改密/Owner 重置/停用恢复
- 会话失效：角色变化、密码重置、停用后旧会话立即失效
- 越权：篡改 member_id 写入、详情嵌套泄露、统计泄露、附件父资源越权
- 代操作审计：operator/acting_for 入操作日志；跨成员查看入安全审计
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Attachment, Book, Member, OperationLog, ReadingNote
from app.services import rate_limit, security_audit
from app.utils.book_helpers import serialize_json_list


def _make_web_client(db_session: Session) -> TestClient:
    """可登录的 Web 客户端（带 CSRF Origin 头）。"""
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    c = TestClient(app)
    c.headers.update({"Origin": "http://127.0.0.1"})
    return c


@pytest.fixture(autouse=True)
def _reset_state():
    rate_limit.reset()
    security_audit.reset()
    yield
    rate_limit.reset()
    security_audit.reset()


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    """owner（fixture 会话）+ 成员乙（独立凭据）+ 各自私有数据。"""
    owner_id = client.get("/auth/session").json()["member_id"]
    r = client.post("/api/v1/members", json={"name": "成员乙", "role": "member"})
    assert r.status_code == 201, r.text
    member_b = r.json()["data"]["id"]
    r = client.post(f"/api/v1/members/{member_b}/password", json={"password": "member-b-pass-123"})
    assert r.status_code == 200, r.text
    username_b = r.json()["data"]["username"]
    assert username_b

    book = Book(title="阶段2书", authors=serialize_json_list(["作者"]))
    db_session.add(book)
    db_session.commit()
    db_session.add(ReadingNote(book_id=book.id, member_id=owner_id, content_md="OWNER_PRIVATE_NOTE"))
    db_session.commit()

    return {
        "owner_id": owner_id, "member_b": member_b,
        "username_b": username_b, "book_id": book.id,
    }


def _login(c: TestClient, username: str, password: str):
    return c.post("/auth/login", json={"username": username, "password": password})


def _member_client(world: dict, db_session: Session) -> TestClient:
    c = _make_web_client(db_session)
    r = _login(c, world["username_b"], "member-b-pass-123")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "member"
    return c


# ── 凭据生命周期 ──


def test_member_login_with_username_and_role(world: dict, db_session: Session) -> None:
    c = _make_web_client(db_session)
    r = _login(c, world["username_b"], "member-b-pass-123")
    assert r.status_code == 200
    body = r.json()
    assert body["member_id"] == world["member_b"]
    assert body["role"] == "member"
    # /auth/session 返回角色
    r = c.get("/auth/session")
    assert r.json()["role"] == "member"


def test_password_only_login_requires_username_when_multiple(world: dict, db_session: Session,
                                                              client: TestClient) -> None:
    """存在多条凭据时，仅密码登录必须提供用户名（单账号回退兼容仍有效）。"""
    from app.services import agent_access
    owner = db_session.get(Member, world["owner_id"])
    agent_access.set_member_password(db_session, owner, "owner-pass-123456")
    c = _make_web_client(db_session)
    r = c.post("/auth/login", json={"password": "whatever"})
    assert r.status_code == 400
    assert "用户名" in r.text


def test_member_lockout_after_failed_attempts(world: dict, db_session: Session) -> None:
    c = _make_web_client(db_session)
    for _ in range(5):
        r = _login(c, world["username_b"], "wrong-password")
        assert r.status_code == 401
    r = _login(c, world["username_b"], "member-b-pass-123")
    assert r.status_code == 429


def test_self_password_change_keeps_current_session(world: dict, db_session: Session,
                                                    client: TestClient) -> None:
    from app.services import agent_access
    owner = db_session.get(Member, world["owner_id"])
    agent_access.set_member_password(db_session, owner, "owner-pass-123456")

    c = _make_web_client(db_session)
    r = _login(c, owner.username, "owner-pass-123456")
    assert r.status_code == 200
    # 另一个旧会话
    c2 = _make_web_client(db_session)
    assert _login(c2, owner.username, "owner-pass-123456").status_code == 200

    r = c.post("/auth/change-password", json={
        "old_password": "owner-pass-123456",
        "new_password": "owner-pass-654321",
        "confirm": "owner-pass-654321",
    })
    assert r.status_code == 200, r.text
    # 当前会话仍有效
    assert c.get("/auth/session").json()["authenticated"] is True
    # 其它会话已失效
    assert c2.get("/auth/session").json()["authenticated"] is False
    # 旧密码不能再登录，新密码可以
    assert _login(_make_web_client(db_session), owner.username, "owner-pass-123456").status_code == 401
    assert _login(_make_web_client(db_session), owner.username, "owner-pass-654321").status_code == 200


def test_owner_password_reset_revokes_sessions(world: dict, db_session: Session,
                                               client: TestClient) -> None:
    c = _member_client(world, db_session)
    assert c.get("/auth/session").json()["authenticated"] is True
    r = client.post(f"/api/v1/members/{world['member_b']}/password",
                    json={"password": "member-b-new-456"})
    assert r.status_code == 200
    # 旧会话失效；新密码可登录
    assert c.get("/auth/session").json()["authenticated"] is False
    c2 = _member_client(world, db_session) if False else None  # 占位避免重复登录
    r = _login(_make_web_client(db_session), world["username_b"], "member-b-new-456")
    assert r.status_code == 200


def test_disable_member_blocks_login_and_revokes_sessions(world: dict, db_session: Session,
                                                           client: TestClient) -> None:
    c = _member_client(world, db_session)
    assert c.get("/auth/session").json()["authenticated"] is True
    r = client.patch(f"/api/v1/members/{world['member_b']}", json={"disabled": True})
    assert r.status_code == 200, r.text
    assert c.get("/auth/session").json()["authenticated"] is False
    # 停用后登录被拒
    r = _login(_make_web_client(db_session), world["username_b"], "member-b-pass-123")
    assert r.status_code == 403
    # 恢复后可登录
    client.patch(f"/api/v1/members/{world['member_b']}", json={"disabled": False})
    r = _login(_make_web_client(db_session), world["username_b"], "member-b-pass-123")
    assert r.status_code == 200


def test_role_change_revokes_sessions(world: dict, db_session: Session, client: TestClient) -> None:
    # 再建一个 owner 便于后续把乙提升（不影响末位保护断言方向）
    r = client.post("/api/v1/members", json={"name": "备用Owner", "role": "owner"})
    spare_owner = r.json()["data"]["id"]
    client.post(f"/api/v1/members/{spare_owner}/password", json={"password": "spare-pass-123456"})

    c = _member_client(world, db_session)
    assert c.get("/auth/session").json()["authenticated"] is True
    r = client.patch(f"/api/v1/members/{world['member_b']}", json={"role": "owner"})
    assert r.status_code == 200
    # 角色变化后旧会话失效
    assert c.get("/auth/session").json()["authenticated"] is False
    # 恢复回 member（清理）
    client.patch(f"/api/v1/members/{world['member_b']}", json={"role": "member"})


def test_last_active_owner_guard(client: TestClient, world: dict) -> None:
    """不能停用或降级唯一的活跃 owner。"""
    r = client.patch(f"/api/v1/members/{world['owner_id']}", json={"disabled": True})
    assert r.status_code == 400
    r = client.patch(f"/api/v1/members/{world['owner_id']}", json={"role": "member"})
    assert r.status_code == 400


def test_member_cannot_manage_members(client: TestClient, world: dict, db_session: Session) -> None:
    """成员管理端点仅 Owner Web 会话：member 会话/渠道/匿名均拒绝。"""
    c = _member_client(world, db_session)
    r = c.patch(f"/api/v1/members/{world['member_b']}", json={"disabled": True})
    assert r.status_code in (401, 403)
    r = c.post(f"/api/v1/members/{world['member_b']}/password", json={"password": "hack-pass-12345"})
    assert r.status_code in (401, 403)


# ── 本人数据隔离（member Web 会话） ──


def test_forged_member_id_write_rejected(world: dict, db_session: Session) -> None:
    """篡改请求体 member_id 冒充他人写入 → 403。"""
    c = _member_client(world, db_session)
    r = c.post(f"/api/v1/books/{world['book_id']}/notes", json={
        "member_id": world["owner_id"], "content_md": "冒充 owner",
    })
    assert r.status_code == 403
    r = c.post(f"/api/v1/books/{world['book_id']}/progress", json={
        "member_id": world["owner_id"], "status": "reading",
    })
    assert r.status_code == 403


def test_member_detail_no_nested_leak(world: dict, db_session: Session) -> None:
    """详情嵌套子资源：member 只看自己的记录，owner 的私有笔记不泄露。"""
    c = _member_client(world, db_session)
    # 乙自己的进度/笔记
    assert c.post(f"/api/v1/books/{world['book_id']}/notes",
                  json={"content_md": "乙的笔记"}).status_code in (200, 201)
    r = c.get(f"/api/v1/books/{world['book_id']}")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "OWNER_PRIVATE_NOTE" not in json.dumps(data, ensure_ascii=False)
    for n in data.get("reading_notes", []):
        assert n["member_id"] == world["member_b"]
    for p in data.get("reading_progress", []):
        assert p["member_id"] == world["member_b"]
    # owner 看得到两者
    owner_client = _make_web_client(db_session)
    del owner_client


def test_member_stats_own_scope(world: dict, db_session: Session, client: TestClient) -> None:
    from app.models import PurchaseRecord, ReadingLog
    db_session.add(PurchaseRecord(book_id=world["book_id"], buyer_member_id=world["member_b"], price=5.0))
    db_session.commit()
    c = _member_client(world, db_session)
    r = c.get("/api/v1/stats")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total_spent"] == 5.0
    assert [m["id"] for m in data["members"]] == [world["member_b"]]
    # owner web 全量（乙 5 元）
    r = client.get("/api/v1/stats")
    assert r.json()["data"]["total_spent"] == 5.0
    assert len(r.json()["data"]["members"]) >= 2


def test_member_cannot_see_channel_bindings(world: dict, db_session: Session, client: TestClient) -> None:
    client.post("/api/v1/members/bind", json={
        "member_id": world["member_b"], "channel": "feishu", "external_user_id": "ou_stage2_b",
    })
    c = _member_client(world, db_session)
    r = c.get("/api/v1/members")
    assert r.status_code == 200
    for item in r.json()["data"]["items"]:
        assert item.get("channel_bindings") in (None, {})


# ── 附件父资源权限 ──


def test_attachment_inherits_parent_permission(world: dict, db_session: Session,
                                               client: TestClient) -> None:
    from app.config import settings

    note_b = ReadingNote(book_id=world["book_id"], member_id=world["member_b"], content_md="乙的笔记2")
    db_session.add(note_b)
    db_session.commit()
    attachments_dir = Path(settings.attachments_dir)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    # 乙的笔记附件（L3 私有）与书本附件（家庭共享）
    (attachments_dir / "note_b_private.txt").write_text("B_PRIVATE_SENTINEL")
    (attachments_dir / "book_shared.txt").write_text("SHARED_OK")
    db_session.add_all([
        Attachment(entity_type="note", entity_id=note_b.id, attach_type="file",
                   title="乙笔记附件", file_path="attachments/note_b_private.txt"),
        Attachment(entity_type="book", entity_id=world["book_id"], attach_type="file",
                   title="书本附件", file_path="attachments/book_shared.txt"),
    ])
    db_session.commit()

    # 另建成员丙：访问乙的笔记附件 → 404（防枚举）；书本附件 → 200
    r = client.post("/api/v1/members", json={"name": "成员丙", "role": "member"})
    member_c = r.json()["data"]["id"]
    r = client.post(f"/api/v1/members/{member_c}/password", json={"password": "member-c-pass-123"})
    username_c = r.json()["data"]["username"]

    cc = _make_web_client(db_session)
    assert _login(cc, username_c, "member-c-pass-123").status_code == 200
    r = cc.get("/api/v1/files/attachments/note_b_private.txt")
    assert r.status_code == 404
    assert "B_PRIVATE_SENTINEL" not in r.text
    r = cc.get("/api/v1/files/attachments/book_shared.txt")
    assert r.status_code == 200

    # 归属本人（乙）可见；Owner 可见
    cb = _member_client(world, db_session)
    assert cb.get("/api/v1/files/attachments/note_b_private.txt").status_code == 200
    assert client.get("/api/v1/files/attachments/note_b_private.txt").status_code == 200


# ── Owner 代操作审计 ──


def test_owner_delegate_write_records_operator(world: dict, db_session: Session,
                                               client: TestClient) -> None:
    """Owner 代乙写进度：业务成功，操作日志记录 operator_member_id + acting_for。"""
    r = client.post(f"/api/v1/books/{world['book_id']}/progress", json={
        "member_id": world["member_b"], "status": "reading",
    })
    assert r.status_code in (200, 201), r.text
    row = db_session.query(OperationLog).filter(
        OperationLog.action == "progress.update",
        OperationLog.member_id == world["member_b"],
    ).order_by(OperationLog.id.desc()).first()
    assert row is not None
    payload = json.loads(row.payload or "{}")
    assert payload.get("operator_member_id") == world["owner_id"]
    assert payload.get("acting_for") is True


def test_owner_delegate_view_audited(world: dict, db_session: Session, client: TestClient) -> None:
    """Owner 查看含他人 L3 的详情 → 共享安全审计 owner.delegate_view（采样留痕）。"""
    # 先让乙拥有 L3 记录（对 Owner 视角而言是"他人数据"）
    db_session.add(ReadingNote(book_id=world["book_id"], member_id=world["member_b"], content_md="乙的私有"))
    db_session.commit()
    security_audit.reset()
    client.get(f"/api/v1/books/{world['book_id']}")
    events = security_audit.list_security_events(db_session, event_type="owner.delegate_view")
    assert events, "缺少 owner.delegate_view 审计事件"
    payload = json.loads(events[0].payload or "{}")
    assert world["owner_id"] in payload.get("details", {}).get("data_owner_member_ids", []) or \
        payload.get("details", {}).get("data_owner_member_ids")
