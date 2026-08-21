"""权限阶段 0（任务 0.6）：Grant 创建/更新入口的 Owner-only 批准规则。

基线 §6.2/§7.1/§14-阶段0-6：Agent Grant 只能由 Owner 批准；
Member（渠道或未来 Web 身份）最多提交申请，不能自批、不能扩大授权。
服务层强制校验批准者角色——即使未来出现新的调用入口也不依赖调用方自觉。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member
from app.services import agent_access, permission_policy
from tests.test_agent_access_e2e import _ensure_owner, _init_owner_password, _login, _logout


def _get_or_create_member(db: Session, name: str, role: str = "member") -> Member:
    member = db.scalar(select(Member).where(Member.name == name, Member.role == role))
    if member is None:
        member = Member(name=name, role=role)
        db.add(member)
        db.commit()
        db.refresh(member)
    return member


def _ensure_member(db: Session, name: str = "普通成员") -> Member:
    return _get_or_create_member(db, name, role="member")


def _register_client(db: Session) -> int:
    client = agent_access.register_agent_client(db, display_name="OwnerOnly 测试 Agent")
    return client.id


# ── 服务层：批准者必须是 Owner ──

def test_create_grant_requires_owner_approver(db_session: Session) -> None:
    """非 Owner 批准者创建 Grant → 403（服务层强制，不依赖 API 层）。"""
    _get_or_create_member(db_session, "Owner", role="owner")
    member = _ensure_member(db_session)
    client_id = _register_client(db_session)

    with pytest.raises(Exception) as exc_info:
        agent_access.create_grant(
            db_session,
            agent_client_id=client_id,
            member_id=member.id,
            scopes=["books:read"],
            approved_by_member_id=member.id,  # member 自批
        )
    assert getattr(exc_info.value, "status_code", None) == 403


def test_create_grant_rejects_missing_approver(db_session: Session) -> None:
    """不传批准者不再默认"绑定成员自批"——缺批准者即拒绝。"""
    member = _ensure_member(db_session)
    client_id = _register_client(db_session)

    with pytest.raises(Exception) as exc_info:
        agent_access.create_grant(
            db_session,
            agent_client_id=client_id,
            member_id=member.id,
            scopes=["books:read"],
        )
    assert getattr(exc_info.value, "status_code", None) == 403


def test_create_grant_owner_approver_succeeds(db_session: Session) -> None:
    """Owner 批准的 Grant 正常创建。"""
    owner = _get_or_create_member(db_session, "Owner-OK", role="owner")
    member = _ensure_member(db_session)
    client_id = _register_client(db_session)

    grant = agent_access.create_grant(
        db_session,
        agent_client_id=client_id,
        member_id=member.id,
        scopes=["books:read"],
        approved_by_member_id=owner.id,
    )
    assert grant.status == "active"
    assert grant.approved_by_member_id == owner.id


def test_create_grant_high_risk_scope_still_owner_only(db_session: Session) -> None:
    """高风险 Scope（books:delete/stats:household）可由 Owner 授予，非 Owner 不行。"""
    owner = _get_or_create_member(db_session, "Owner-HR", role="owner")
    member = _ensure_member(db_session)
    client_id = _register_client(db_session)

    grant = agent_access.create_grant(
        db_session,
        agent_client_id=client_id,
        member_id=member.id,
        scopes=["books:delete", "stats:household"],
        approved_by_member_id=owner.id,
    )
    assert agent_access.get_grant_scopes(grant) == ["books:delete", "stats:household"]

    client2 = _register_client(db_session)
    with pytest.raises(Exception) as exc_info:
        agent_access.create_grant(
            db_session,
            agent_client_id=client2,
            member_id=member.id,
            scopes=["stats:household"],
            approved_by_member_id=member.id,
        )
    assert getattr(exc_info.value, "status_code", None) == 403


# ── 服务层：可授予集合 ──

def test_validate_scopes_rejects_non_grantable(db_session: Session) -> None:
    """不在 AGENT_GRANTABLE_SCOPES 的能力名不能进入 Grant（管理类 Scope 防御）。"""
    with pytest.raises(Exception) as exc_info:
        agent_access.validate_scopes(["books:read", "agent_grants:manage"])
    assert getattr(exc_info.value, "status_code", None) == 400


# ── HTTP 层回归：管理端点对非 Owner 主体关闭 ──

MANAGEMENT_ENDPOINTS = [
    ("POST", "/agent-access/clients", {"display_name": "X"}),
    ("POST", "/agent-access/grants", {"agent_client_id": 1, "member_id": 1, "scopes": ["books:read"]}),
    ("PATCH", "/agent-access/grants/1", {"scopes": ["books:read"]}),
    ("DELETE", "/agent-access/grants/1", None),
    ("POST", "/agent-access/tokens", {"grant_id": 1}),
]


@pytest.mark.parametrize("method,path,body", MANAGEMENT_ENDPOINTS)
def test_management_endpoints_reject_non_owner_subjects(
    client: TestClient, method: str, path: str, body: dict | None
) -> None:
    """Agent Token / 匿名 / 渠道身份均不能触碰授权管理端点（矩阵：owner/web 专用）。"""
    _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # Agent Token（含全量 Scope 的 Grant）
    r = client.post("/agent-access/clients", json={"display_name": "管理面测试 Agent"})
    agent_client_id = r.json()["id"]
    member_id = client.get("/auth/session").json()["member_id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": sorted(permission_policy.ALL_SCOPES),
    })
    grant_id = r.json()["id"]
    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    all_scopes_token = r.json()["token"]
    _logout(client)

    subjects: list[tuple[str, dict | None]] = [
        ("匿名", None),
        ("agent-token", {"Authorization": f"Bearer {all_scopes_token}"}),
        ("channel-owner", {"X-Channel": "feishu", "X-External-User-Id": "ou_owner_mgr"}),
    ]
    for label, headers in subjects:
        # channel-owner 需要 owner 成员绑定该渠道才能通过身份解析
        if label == "channel-owner":
            _login(client)
            client.post("/api/v1/members/bind", json={
                "member_id": member_id, "channel": "feishu", "external_user_id": "ou_owner_mgr",
            })
            _logout(client)
        r = client.request(method, path, json=body, headers=headers)
        assert r.status_code in (401, 403, 404), (
            f"{label} 调用 {method} {path} 应被拒绝（401/403/404），"
            f"实际 {r.status_code}: {r.text}"
        )
