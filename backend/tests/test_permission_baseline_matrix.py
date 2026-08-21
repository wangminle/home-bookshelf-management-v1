"""权限阶段 0（任务 0.2/0.3）：主体 × 动作 × 数据层 × 数据归属 参数化测试表。

权限基线 §14 阶段 0 验收：每个业务端点都有主体、动作、资源层和数据范围定义；
不存在"只写了 Scope 名但没有资源规则"的端点。

本文件是授权矩阵（design/plans/agent-authorization-matrix.md）的可执行形式：
- ENDPOINT_REGISTRY 声明每个端点的 (方法, Scope, 资源层, 数据范围)；
- 参数化用例按主体（匿名 / 缺 Scope Agent / Member 渠道 / Owner 渠道）
  验证允许与拒绝行为；
- 与 test_authorization_matrix.py（Agent Scope 粒度）互补，此处补充
  数据层/数据范围声明与渠道角色缩权口径。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app.models import Member
from app.services import agent_access, permission_policy


# ── 端点注册表（与授权矩阵文档同步维护） ──


@dataclass
class EndpointSpec:
    method: str
    path: str
    scope: str | None  # None = Owner Web 会话专用（授权管理层）
    layer: str  # 资源层：L0-L4（基线 §4.1 五级分层）
    data_scope: str  # 数据范围（基线 §6.3 命名）
    body: Callable[[dict, int], dict] | None = None  # (world, actor_member_id) -> 请求体
    success_codes: frozenset[int] = frozenset({200, 201})
    behavior_probe: bool = True  # False = 仅注册表声明 + 匿名拒绝探针


ENDPOINT_REGISTRY: list[EndpointSpec] = [
    # L1/L2 书目（家庭共享）
    EndpointSpec("GET", "/api/v1/books", "books:read", "L1/L2", "household_shared"),
    EndpointSpec("POST", "/api/v1/books", "books:write", "L2", "household_shared",
                 body=lambda w, m: {"title": "矩阵书"}, success_codes=frozenset({201})),
    EndpointSpec("GET", "/api/v1/books/{book_id}", "books:read", "L1/L2", "household_shared"),
    EndpointSpec("PATCH", "/api/v1/books/{book_id}", "books:write", "L2", "household_shared",
                 body=lambda w, m: {"title": "矩阵书-改"}),
    EndpointSpec("DELETE", "/api/v1/books/{book_id}", "books:delete", "L2", "household_shared",
                 success_codes=frozenset({200, 204})),
    EndpointSpec("POST", "/api/v1/books/{book_id}/merge", "books:delete", "L2", "household_shared",
                 success_codes=frozenset({200})),
    EndpointSpec("POST", "/api/v1/books/intake/json", "books:write", "L2", "household_shared",
                 body=lambda w, m: {"title": "矩阵入库书"}, success_codes=frozenset({200, 201})),
    EndpointSpec("POST", "/api/v1/custom-fields", "books:write", "L2", "household_shared",
                 body=lambda w, m: {"entity_type": "book", "entity_id": w["book_id"], "field_key": "k", "field_value": "v"}),
    EndpointSpec("POST", "/api/v1/books/{book_id}/copies", "books:write", "L2", "household_shared",
                 body=lambda w, m: {"owner_member_id": m}, success_codes=frozenset({201})),
    # L3 成员私有（必须归属个人；body 的 member_id 跟随主体本人）
    EndpointSpec("POST", "/api/v1/books/{book_id}/progress", "reading:write", "L3", "self(member)",
                 body=lambda w, m: {"member_id": m, "status": "reading"}, success_codes=frozenset({200, 201})),
    EndpointSpec("POST", "/api/v1/books/{book_id}/reading-logs", "reading:write", "L3", "self(member)",
                 body=lambda w, m: {"member_id": m, "log_date": "2026-08-21", "pages": 10}, success_codes=frozenset({200, 201})),
    EndpointSpec("POST", "/api/v1/books/{book_id}/notes", "notes:write", "L3", "self(member)",
                 body=lambda w, m: {"member_id": m, "content_md": "矩阵笔记"}, success_codes=frozenset({200, 201})),
    EndpointSpec("POST", "/api/v1/books/{book_id}/purchases", "purchases:write", "L3", "self(member)",
                 body=lambda w, m: {"member_id": m, "price": 10}, success_codes=frozenset({200, 201})),
    # multipart 表单端点：仅注册表声明 + 匿名拒绝（成功路径需表单/文件，专项测试覆盖）
    EndpointSpec("POST", "/api/v1/attachments", "notes:write", "L3", "self(member)", behavior_probe=False),
    # 统计 / 成员 / 诊断 / 文件
    EndpointSpec("GET", "/api/v1/stats", "stats:read", "L2/L3", "self(member)；家庭聚合另需 stats:household"),
    EndpointSpec("GET", "/api/v1/members", "members:read", "L4（channel_bindings 仅 owner 可见）", "members_basic"),
    EndpointSpec("GET", "/api/v1/health", "members:read", "L4", "全局诊断"),
    EndpointSpec("GET", "/api/v1/files/covers/nonexistent.jpg", "files:read", "L1（缩略图）/L3（原件）", "继承父资源",
                 success_codes=frozenset({200, 404})),
    # multipart/识别端点：仅注册表声明 + 匿名拒绝（成功路径需文件上传，专项测试覆盖）
    EndpointSpec("POST", "/api/v1/recognize/isbn", "books:write", "L0（无数据写入）", "无", behavior_probe=False),
    EndpointSpec("POST", "/api/v1/books/{book_id}/cover", "books:write", "L2", "household_shared", behavior_probe=False),
    EndpointSpec("POST", "/api/v1/books/intake", "books:write", "L2", "household_shared", behavior_probe=False),
    # L4 授权管理（Owner Web 会话专用；行为面由 test_grant_owner_only / test_agent_access_management 覆盖）
    EndpointSpec("POST", "/agent-access/grants", None, "L4", "管理（owner web 专用）", behavior_probe=False),
    EndpointSpec("PATCH", "/agent-access/grants/1", None, "L4", "管理（owner web 专用）", behavior_probe=False),
    EndpointSpec("DELETE", "/agent-access/grants/1", None, "L4", "管理（owner web 专用）", behavior_probe=False),
    EndpointSpec("POST", "/agent-access/tokens", None, "L4", "管理（owner web 专用）", behavior_probe=False),
]


# ── 注册表完整性（阶段 0 验收：无"只有 Scope 名没有资源规则"的端点） ──


def test_registry_has_no_blank_resource_rules() -> None:
    seen: set[tuple[str, str]] = set()
    for spec in ENDPOINT_REGISTRY:
        assert spec.layer.strip(), f"{spec.method} {spec.path} 缺资源层定义"
        assert spec.data_scope.strip(), f"{spec.method} {spec.path} 缺数据范围定义"
        key = (spec.method, spec.path)
        assert key not in seen, f"注册表重复端点: {key}"
        seen.add(key)


def test_registry_scopes_are_known_or_owner_only() -> None:
    for spec in ENDPOINT_REGISTRY:
        if spec.scope is not None:
            assert spec.scope in permission_policy.ALL_SCOPES, (
                f"{spec.method} {spec.path} 声明了未知 Scope: {spec.scope}"
            )


# ── 测试世界：owner/member/渠道绑定/探针书 就绪 ──


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict[str, Any]:
    w: dict[str, Any] = {}
    owner_id = client.get("/auth/session").json()["member_id"]
    w["owner_id"] = owner_id

    r = client.post("/api/v1/members", json={"name": "矩阵成员", "role": "member"})
    assert r.status_code == 201, r.text
    member_id = r.json()["data"]["id"]
    w["member_id"] = member_id

    client.post("/api/v1/members/bind", json={
        "member_id": member_id, "channel": "feishu", "external_user_id": "ou_matrix_member",
    })
    client.post("/api/v1/members/bind", json={
        "member_id": owner_id, "channel": "feishu", "external_user_id": "ou_matrix_owner",
    })
    w["member_channel"] = {"X-Channel": "feishu", "X-External-User-Id": "ou_matrix_member"}
    w["owner_channel"] = {"X-Channel": "feishu", "X-External-User-Id": "ou_matrix_owner"}

    r = client.post("/api/v1/books", json={"title": "矩阵探针书"})
    assert r.status_code == 201, r.text
    w["book_id"] = r.json()["data"]["id"]
    return w


def _fresh_book(client: TestClient) -> int:
    r = client.post("/api/v1/books", json={"title": "矩阵一次性书"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _missing_scope_agent_headers(scope_to_avoid: str | None) -> dict[str, str]:
    """签发一个只持有 scope_to_avoid 之外某个 Scope 的 Agent Token。"""
    candidate = next(
        s for s in sorted(permission_policy.ALL_SCOPES) if s != scope_to_avoid
    )
    with db_module.SessionLocal() as db:
        owner = db.scalar(select(Member).where(Member.role == "owner"))
        assert owner is not None
        client_row = agent_access.register_agent_client(db, display_name=f"缺Scope探针-{candidate}")
        grant = agent_access.create_grant(
            db,
            agent_client_id=client_row.id,
            member_id=owner.id,
            scopes=[candidate],
            approved_by_member_id=owner.id,
        )
        token, _ = agent_access.issue_token(db, grant.id)
    return {"Authorization": f"Bearer {token}"}


def _build_request(
    spec: EndpointSpec, client: TestClient, w: dict, actor_member_id: int
) -> tuple[str, dict | None]:
    """构造端点请求（处理路径变量与破坏性端点的一次性资源），返回 (path, body)。"""
    path = spec.path
    if "/merge" in path:
        # 破坏性：target=探针书，source=一次性新书
        source_id = _fresh_book(client)
        path = path.replace("{book_id}", str(w["book_id"]))
        return f"{path}?source_id={source_id}", None
    if spec.method == "DELETE":
        path = path.replace("{book_id}", str(_fresh_book(client)))
    elif "{book_id}" in path:
        path = path.replace("{book_id}", str(w["book_id"]))
    body = spec.body(w, actor_member_id) if spec.body else None
    return path, body


# ── 参数化主体行为矩阵 ──


SUBJECTS = ["anonymous", "agent_missing_scope", "member_channel", "owner_channel"]


@pytest.mark.parametrize("spec", ENDPOINT_REGISTRY, ids=lambda s: f"{s.method}-{s.path}")
@pytest.mark.parametrize("subject", SUBJECTS)
def test_subject_action_matrix(
    client: TestClient, anon_client: TestClient, world: dict, spec: EndpointSpec, subject: str
) -> None:
    """主体 × 动作矩阵：各主体对每个端点的允许/拒绝行为。"""
    if subject == "anonymous":
        # 匿名对所有业务端点（含 behavior_probe=False）一律拒绝。
        # 注意用 anon_client：client 夹具注入了 owner 会话 Cookie。
        path = spec.path.replace("{book_id}", str(world["book_id"]))
        r = anon_client.request(spec.method, path)
        assert r.status_code in (401, 403), (
            f"匿名 {spec.method} {spec.path} 应被拒绝，实际 {r.status_code}: {r.text}"
        )
        return

    if not spec.behavior_probe:
        pytest.skip("multipart/管理端点：仅注册表声明，行为面由专项测试覆盖")

    if subject == "agent_missing_scope":
        headers = _missing_scope_agent_headers(spec.scope)
        actor_member_id = world["owner_id"]  # Agent 绑定 owner 成员
    elif subject == "member_channel":
        headers = world["member_channel"]
        actor_member_id = world["member_id"]
    else:
        headers = world["owner_channel"]
        actor_member_id = world["owner_id"]

    path, body = _build_request(spec, client, world, actor_member_id)
    r = client.request(spec.method, path, json=body, headers=headers)

    if subject == "agent_missing_scope":
        assert r.status_code == 403, (
            f"缺 Scope Agent {spec.method} {spec.path} 应 403，实际 {r.status_code}: {r.text}"
        )
    elif subject == "member_channel":
        if spec.scope is None or spec.scope not in permission_policy.MEMBER_ROLE_SCOPES:
            assert r.status_code == 403, (
                f"Member 渠道 {spec.method} {spec.path}（scope={spec.scope}）应 403，"
                f"实际 {r.status_code}: {r.text}"
            )
        else:
            assert r.status_code in spec.success_codes, (
                f"Member 渠道 {spec.method} {spec.path}（scope={spec.scope}）应成功"
                f"{sorted(spec.success_codes)}，实际 {r.status_code}: {r.text}"
            )
    else:  # owner_channel：业务端点全量能力；管理层仅 Owner Web 会话
        if spec.scope is None:
            assert r.status_code in (401, 403), (
                f"Owner 渠道访问管理端点 {spec.path} 应被拒绝（仅 Owner Web 会话），"
                f"实际 {r.status_code}"
            )
        else:
            assert r.status_code in spec.success_codes, (
                f"Owner 渠道 {spec.method} {spec.path} 应成功{sorted(spec.success_codes)}，"
                f"实际 {r.status_code}: {r.text}"
            )


# ── Owner Web 会话回归：全量业务能力（既有行为） ──


def test_owner_web_can_access_business_endpoints(client: TestClient, world: dict) -> None:
    """Owner Web 会话（fixture 注入）对业务端点全量可用——渠道缩权不波及 Web Owner。"""
    r = client.get("/api/v1/books")
    assert r.status_code == 200
    r = client.get("/api/v1/stats")
    assert r.status_code == 200
    r = client.delete(f"/api/v1/books/{_fresh_book(client)}")
    assert r.status_code in (200, 204)
    r = client.get("/api/v1/members")
    assert r.status_code == 200
    # Web owner 可见 channel_bindings（BUG-113 口径）
    items = r.json()["data"]["items"]
    assert any(item.get("channel_bindings") for item in items)
