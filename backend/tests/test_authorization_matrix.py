"""WBS-6：授权矩阵参数化测试。

验证全部业务端点 × 方法 × Scope 组合：
- 无凭证 -> 401/403
- 有 Token 缺 Scope -> 403
- 有 Token 有 Scope -> 成功（200/201）
- 伪造 X-UI-Client -> 401/403
- member_id 不匹配 -> 403
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_agent_access_e2e import (
    _ensure_owner,
    _init_owner_password,
    _login,
    _logout,
)


def _setup_agent(client: TestClient, scopes: list[str]) -> tuple[int, str]:
    """创建 owner + Agent + Grant + Token，返回 (member_id, token)。"""
    member_id = _ensure_owner(client)

    # 仅在密码未初始化时设置
    r = client.get("/auth/status")
    if r.status_code == 200 and not r.json().get("password_initialized"):
        _init_owner_password(client)
    _login(client)

    r = client.post("/agent-access/clients", json={"display_name": "Matrix Test Agent"})
    assert r.status_code == 200, f"创建 Agent Client 失败: {r.text}"
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": scopes,
    })
    assert r.status_code == 200, f"创建 Grant 失败: {r.text}"
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    assert r.status_code == 200, f"签发 Token 失败: {r.text}"
    token = r.json()["token"]

    # 绑定渠道（使系统进入"已建立绑定"状态，后续无凭证请求将被拒绝）
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_matrix"},
    )
    return member_id, token


def _create_book(client: TestClient, member_id: int, token: str) -> int:
    """用 Agent Token 创建一本书，返回 book_id。"""
    r = client.post(
        "/api/v1/books",
        json={"title": "矩阵测试书", "member_id": member_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, f"创建测试书籍失败: {r.text}"
    return r.json()["data"]["id"]


# ── 无凭证访问 ──

WRITE_ENDPOINTS = [
    ("POST", "/api/v1/books", {"title": "test"}),
    ("POST", "/api/v1/books/1/copies", {"owner_member_id": 1}),
    ("POST", "/api/v1/books/1/notes", {"content_md": "test"}),
    ("POST", "/api/v1/books/1/progress", {"status": "reading"}),
    ("POST", "/api/v1/books/1/reading-logs", {"log_date": "2026-08-11"}),
    ("POST", "/api/v1/books/1/purchases", {"price": 10}),
    ("POST", "/api/v1/custom-fields", {"entity_type": "book", "entity_id": 1, "field_key": "k", "field_value": "v"}),
]


@pytest.mark.parametrize("method,path,body", WRITE_ENDPOINTS)
def test_write_endpoints_require_auth(client: TestClient, method: str, path: str, body: dict):
    """无凭证的写端点必须返回 401 或 403（建立绑定后）。"""
    # 先建立绑定，使系统进入"要求认证"状态
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_auth_test"},
    )
    _logout(client)

    r = client.request(method, path, json=body)
    assert r.status_code in (401, 403), (
        f"{method} {path} 无凭证应返回 401/403，实际 {r.status_code}: {r.text}"
    )


def test_fake_ui_client_header_rejected(client: TestClient):
    """伪造 X-UI-Client: web 不能获得权限。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_fake_ui"},
    )
    _logout(client)

    r = client.post(
        "/api/v1/books",
        json={"title": "fake ui test"},
        headers={"X-UI-Client": "web"},
    )
    assert r.status_code in (401, 403), (
        f"伪造 X-UI-Client 应被拒绝，实际 {r.status_code}"
    )


# ── Scope 矩阵 ──

SCOPE_MATRIX = [
    # (endpoint, method, required_scope, body)
    ("/api/v1/books", "POST", "books:write", {"title": "scope test"}),
    ("/api/v1/books/{book_id}/notes", "POST", "notes:write", {"content_md": "test"}),
    ("/api/v1/books/{book_id}/progress", "POST", "reading:write", {"status": "reading"}),
    ("/api/v1/books/{book_id}/reading-logs", "POST", "reading:write", {"log_date": "2026-08-11"}),
    ("/api/v1/books/{book_id}/purchases", "POST", "purchases:write", {"price": 10}),
]


@pytest.mark.parametrize("path,method,required_scope,body", SCOPE_MATRIX)
def test_correct_scope_succeeds(client: TestClient, path: str, method: str, required_scope: str, body: dict):
    """有正确 Scope 的 Token 应成功调用。"""
    member_id, token = _setup_agent(client, [required_scope, "books:write"])

    # 先用 books:write scope 创建一本书
    book_id = _create_book(client, member_id, token)
    actual_path = path.replace("{book_id}", str(book_id))

    headers = {"Authorization": f"Bearer {token}"}
    r = client.request(method, actual_path, json=body, headers=headers)
    assert r.status_code in (200, 201), (
        f"{method} {actual_path} 有 scope={required_scope} 应成功，实际 {r.status_code}: {r.text}"
    )
    _logout(client)


@pytest.mark.parametrize("path,method,required_scope,body", SCOPE_MATRIX)
def test_wrong_scope_rejected(client: TestClient, path: str, method: str, required_scope: str, body: dict):
    """只有 books:read 的 Token 不应能调用写端点。"""
    member_id, token = _setup_agent(client, ["books:read", "books:write"])

    # 创建一本书（需要 books:write）
    book_id = _create_book(client, member_id, token)
    actual_path = path.replace("{book_id}", str(book_id))

    # 重新创建一个只有 books:read 的 Agent
    r = client.post("/agent-access/clients", json={"display_name": "Read Only Agent"})
    read_only_client_id = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": read_only_client_id,
        "member_id": member_id,
        "scopes": ["books:read"],
    })
    read_only_grant_id = r.json()["id"]
    r = client.post("/agent-access/tokens", json={"grant_id": read_only_grant_id})
    read_only_token = r.json()["token"]

    headers = {"Authorization": f"Bearer {read_only_token}"}
    r = client.request(method, actual_path, json=body, headers=headers)
    assert r.status_code == 403, (
        f"{method} {actual_path} 只有 books:read 应返回 403，实际 {r.status_code}"
    )
    _logout(client)


def test_read_scope_allows_get(client: TestClient):
    """books:read scope 应允许 GET /api/v1/books。"""
    member_id, token = _setup_agent(client, ["books:read"])
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    _logout(client)


def test_no_write_scope_token_cannot_write(client: TestClient):
    """只有 books:read（无 books:write）的 Token 不能创建书籍。"""
    member_id, token = _setup_agent(client, ["books:read"])
    r = client.post(
        "/api/v1/books",
        json={"title": "no write scope test", "member_id": member_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    _logout(client)


def test_wrong_member_rejected(client: TestClient):
    """Agent Token 不能操作其他成员的数据。"""
    # BUG-221：conftest client 已有 owner 会话，直接用（跳过 _login 避免域差异）
    member_id = _ensure_owner(client)
    r = client.post("/api/v1/members", json={"name": "other", "role": "member"})
    assert r.status_code == 201, f"创建成员失败: {r.text}"
    other_id = r.json()["data"]["id"]

    # 再设置 Agent（会建立渠道绑定）
    member_id, token = _setup_agent(client, ["books:write", "notes:write"])

    # 创建一本书
    book_id = _create_book(client, member_id, token)

    # 用 Agent Token 但指定 other_member_id 创建笔记
    r = client.post(
        f"/api/v1/books/{book_id}/notes",
        json={"member_id": other_id, "content_md": "cross member"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    _logout(client)


def test_revoked_token_immediately_invalid(client: TestClient):
    """撤销 Token 后下一请求立即失败。"""
    member_id, token = _setup_agent(client, ["books:read"])

    # Token 有效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # 撤销 Grant
    r = client.get("/agent-access/grants")
    grant_id = r.json()[0]["id"]
    client.delete(f"/agent-access/grants/{grant_id}")

    # Token 立即失效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    _logout(client)
