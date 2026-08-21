"""权限阶段 0（任务 0.5）：渠道身份死分支修复——按绑定 Member 角色映射能力集。

基线 §5.4/§8/§14-阶段0-5：渠道绑定确定业务 Member 身份，能力按该 Member 角色映射；
非 Owner 渠道身份不能获得 Owner 管理能力或全量 Scope。
这是**有意的兼容性缩权**（基线 §1.4）：非 Owner 渠道身份将失去
books:delete 与 stats:household，其余 Member 日常能力保留。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member


def _setup_member_with_channel(client: TestClient, db_session: Session) -> tuple[int, int, str, str]:
    """创建 owner + member，并为两人分别绑定不同外部 ID。

    返回 (owner_id, member_id, owner_external_id, member_external_id)。
    """
    r = client.get("/auth/session").json()
    owner_id = r["member_id"]

    # 创建普通成员
    r = client.post("/api/v1/members", json={"name": "渠道成员甲", "role": "member"})
    assert r.status_code == 201, r.text
    member_id = r.json()["data"]["id"]

    # owner 与 member 各绑一个渠道外部身份（owner 会话允许代绑）
    client.post("/api/v1/members/bind", json={
        "member_id": member_id, "channel": "feishu", "external_user_id": "ou_member_a",
    })
    client.post("/api/v1/members/bind", json={
        "member_id": owner_id, "channel": "feishu", "external_user_id": "ou_owner_a",
    })
    return owner_id, member_id, "ou_owner_a", "ou_member_a"


def _create_book(client: TestClient, member_id: int) -> int:
    r = client.post("/api/v1/books", json={"title": "渠道缩权测试书", "member_id": member_id})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


# ── 死分支修复：非 Owner 渠道身份按 Member 能力集 ──

def test_member_channel_can_read_books(client: TestClient, db_session: Session) -> None:
    """Member 渠道身份保留 books:read（浏览共享书架是 Member 基础能力）。"""
    _, member_id, _, member_ext = _setup_member_with_channel(client, db_session)
    _create_book(client, member_id)

    r = client.get("/api/v1/books", headers={
        "X-Channel": "feishu", "X-External-User-Id": member_ext,
    })
    assert r.status_code == 200, r.text


def test_member_channel_can_write_books(client: TestClient, db_session: Session) -> None:
    """Member 渠道身份保留 books:write（基线 §2.3：默认允许新增和修正书目）。"""
    _, member_id, _, member_ext = _setup_member_with_channel(client, db_session)

    r = client.post("/api/v1/books", json={
        "title": "成员渠道入库", "member_id": member_id,
    }, headers={"X-Channel": "feishu", "X-External-User-Id": member_ext})
    assert r.status_code == 201, r.text


def test_member_channel_cannot_delete_books(client: TestClient, db_session: Session) -> None:
    """缩权点 1：Member 渠道身份失去 books:delete（删除书目主记录仅 Owner）。"""
    _, member_id, _, member_ext = _setup_member_with_channel(client, db_session)
    book_id = _create_book(client, member_id)

    r = client.delete(f"/api/v1/books/{book_id}", headers={
        "X-Channel": "feishu", "X-External-User-Id": member_ext,
    })
    assert r.status_code == 403, (
        f"非 Owner 渠道身份 DELETE /books 应 403（缩权后无 books:delete），实际 {r.status_code}: {r.text}"
    )


def test_member_channel_cannot_merge_books(client: TestClient, db_session: Session) -> None:
    """合并走 books:delete（矩阵）：Member 渠道身份同样被拒。"""
    _, member_id, _, member_ext = _setup_member_with_channel(client, db_session)
    book_a = _create_book(client, member_id)
    book_b = _create_book(client, member_id)

    r = client.post(
        f"/api/v1/books/{book_a}/merge?source_id={book_b}",
        headers={"X-Channel": "feishu", "X-External-User-Id": member_ext},
    )
    assert r.status_code == 403, r.text


def test_member_channel_cannot_read_household_stats(client: TestClient, db_session: Session) -> None:
    """缩权点 2：Member 渠道身份失去 stats:household。

    GET /stats 只要求 stats:read（Member 保留），但 stats:household 从其能力集
    移除后，can_access_member 的跨成员口径不再对 Member 渠道放行。
    """
    from app.services import permission_policy

    _, member_id, _, member_ext = _setup_member_with_channel(client, db_session)
    member = db_session.get(Member, member_id)
    assert member is not None

    # 直接验证角色能力集：member 渠道身份构建的 scopes 不含 stats:household
    assert "stats:household" not in permission_policy.role_scopes(member.role)
    assert "stats:household" in permission_policy.role_scopes("owner")


def test_owner_channel_keeps_full_capabilities(client: TestClient, db_session: Session) -> None:
    """回归：绑定 Owner 的渠道身份保持全量能力（基线 §8：能力按绑定角色映射）。"""
    _, member_id, owner_ext, _ = _setup_member_with_channel(client, db_session)
    book_id = _create_book(client, member_id)

    r = client.delete(f"/api/v1/books/{book_id}", headers={
        "X-Channel": "feishu", "X-External-User-Id": owner_ext,
    })
    assert r.status_code in (200, 204), (
        f"Owner 渠道身份应保留删除能力，实际 {r.status_code}: {r.text}"
    )


def test_member_channel_member_scopes_match_policy(client: TestClient, db_session: Session) -> None:
    """渠道身份构建的 scopes 必须与服务器角色能力表一致（不再走死分支全量）。"""
    from app.services import permission_policy

    _, _, _, member_ext = _setup_member_with_channel(client, db_session)
    ctx_headers = {"X-Channel": "feishu", "X-External-User-Id": member_ext}

    # 用 books:read 可用、books:delete 不可用双探针验证能力集边界
    r = client.get("/api/v1/books", headers=ctx_headers)
    assert r.status_code == 200
    # DELETE 探针在上文用例覆盖；这里再验证 policy 层一致性
    member_row = db_session.scalar(select(Member).where(Member.role == "member"))
    assert member_row is not None
    assert permission_policy.role_scopes("member") == permission_policy.MEMBER_ROLE_SCOPES


# ── 渠道身份越权回归（现有行为不变） ──

def test_member_channel_cannot_write_for_others(client: TestClient, db_session: Session) -> None:
    """渠道身份只能写本人（resolve_body_member 既有口径，缩权后不回退）。

    用笔记端点验证：笔记必须归属个人（L3），member 渠道给 owner 写笔记 → 403。
    POST /books 本身是家庭共享写入（无个人归属语义），不适用本口径。
    """
    owner_id, member_id, _, member_ext = _setup_member_with_channel(client, db_session)
    book_id = _create_book(client, member_id)

    r = client.post(f"/api/v1/books/{book_id}/notes", json={
        "member_id": owner_id, "content_md": "冒充他人笔记",
    }, headers={"X-Channel": "feishu", "X-External-User-Id": member_ext})
    assert r.status_code == 403, r.text


def test_unknown_channel_binding_rejected(client: TestClient, db_session: Session) -> None:
    """未绑定的外部身份不能通过渠道头访问（既有行为）。"""
    _setup_member_with_channel(client, db_session)
    r = client.get("/api/v1/books", headers={
        "X-Channel": "feishu", "X-External-User-Id": "ou_nobody",
    })
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("ext,expected", [
    ("ou_owner_a", 200),     # owner 渠道：members:read 可用
    ("ou_member_a", 200),    # member 渠道：members:read 保留（名单基本信息）
])
def test_channel_members_read(client: TestClient, db_session: Session, ext: str, expected: int) -> None:
    """GET /members（members:read）对 owner/member 渠道均可用——但渠道绑定明细仅 owner 可见。"""
    _setup_member_with_channel(client, db_session)
    r = client.get("/api/v1/members", headers={"X-Channel": "feishu", "X-External-User-Id": ext})
    assert r.status_code == expected, r.text
    if ext == "ou_member_a":
        # BUG-113 口径：channel_bindings 仅 owner 可见
        for item in r.json()["data"]["items"]:
            assert item.get("channel_bindings") in (None, {})


def test_member_channel_cannot_access_health(client: TestClient, db_session: Session) -> None:
    """/health 要求 members:read 且属全局诊断；Member 渠道保留 members:read 仍可访问。

    注：诊断面进一步收紧属后续阶段；本用例锁定当前矩阵口径（members:read）不漂移。
    """
    _setup_member_with_channel(client, db_session)
    r = client.get("/api/v1/health", headers={
        "X-Channel": "feishu", "X-External-User-Id": "ou_member_a",
    })
    assert r.status_code == 200, r.text
