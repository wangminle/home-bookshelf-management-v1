"""WBS-9：Agent 访问控制端到端测试。

测试完整流程：
1. Owner 初始化密码 -> 登录 -> 创建 Agent Client -> 创建 Grant -> 签发 Token
2. Agent 使用 Token 访问业务端点
3. Token 撤销后访问被拒绝
4. Scope 限制验证
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _ensure_owner(client: TestClient) -> int:
    """确保有 owner 成员，返回 member_id。"""
    # 先检查是否已有成员
    r = client.get("/api/v1/members")
    if r.status_code == 200:
        items = r.json().get("data", {}).get("items", [])
        if items:
            owner = next((m for m in items if m.get("role") == "owner"), None)
            if owner:
                return owner["id"]
    # 创建 owner
    r = client.post("/api/v1/members", json={"name": "owner", "role": "owner"})
    assert r.status_code == 201
    return r.json()["data"]["id"]


def _init_owner_password(client: TestClient, password: str = "test-password-123") -> None:
    """初始化 owner 密码（升级场景需携带 SETUP_TOKEN）。"""
    from app.config import settings

    headers: dict[str, str] = {}
    if settings.setup_token:
        headers["X-Setup-Token"] = settings.setup_token
    r = client.post("/auth/init-password", json={"password": password, "confirm": password}, headers=headers)
    assert r.status_code == 200, r.text


def _login(client: TestClient, password: str = "test-password-123") -> None:
    """Owner 登录。"""
    r = client.post("/auth/login", json={"password": password})
    assert r.status_code == 200, r.text


def _logout(client: TestClient) -> None:
    client.post("/auth/logout")


def test_owner_password_init_and_login(client: TestClient):
    """Owner 密码初始化和登录流程。"""
    _ensure_owner(client)

    # 检查状态：未初始化
    r = client.get("/auth/status")
    assert r.status_code == 200
    assert r.json()["password_initialized"] is False

    # 初始化密码
    _init_owner_password(client)

    # 检查状态：已初始化
    r = client.get("/auth/status")
    assert r.json()["password_initialized"] is True

    # 不能重复初始化
    r = client.post("/auth/init-password", json={"password": "another-123", "confirm": "another-123"})
    assert r.status_code == 400

    # 登录
    _login(client)

    # 检查会话
    r = client.get("/auth/session")
    assert r.status_code == 200
    assert r.json()["authenticated"] is True

    # 退出
    _logout(client)

    # 检查会话已过期
    r = client.get("/auth/session")
    assert r.json()["authenticated"] is False


def test_login_wrong_password(client: TestClient):
    """错误密码登录失败。"""
    _ensure_owner(client)
    _init_owner_password(client)

    r = client.post("/auth/login", json={"password": "wrong-password"})
    assert r.status_code == 401


def test_agent_client_grant_token_flow(client: TestClient):
    """完整 Agent 授权流程：Client -> Grant -> Token -> 使用。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # 1. 创建 Agent Client
    r = client.post("/agent-access/clients", json={
        "display_name": "Test Agent",
        "client_type": "codex",
    })
    assert r.status_code == 200, r.text
    client_data = r.json()
    agent_client_id = client_data["id"]
    assert client_data["display_name"] == "Test Agent"

    # 2. 创建 Grant
    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read", "books:write"],
        "expires_in_days": 7,
    })
    assert r.status_code == 200, r.text
    grant_data = r.json()
    grant_id = grant_data["id"]
    assert grant_data["status"] == "active"
    assert "books:read" in grant_data["scopes"]

    # 3. 签发 Token
    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    assert r.status_code == 200, r.text
    token_data = r.json()
    token = token_data["token"]
    assert token.startswith("hbs_at_")
    assert token_data["token_prefix"].startswith("hbs_at_")

    # 4. 使用 Token 访问业务端点
    # 先绑定渠道（因为系统需要绑定才能允许写入）
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_test"},
    )

    # 用 Agent Token 创建书籍
    r = client.post(
        "/api/v1/books",
        json={"title": "Agent 测试书", "member_id": member_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, f"Agent Token 应能创建书籍: {r.text}"

    # 用 Agent Token 查询书籍
    r = client.get(
        "/api/v1/books",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    _logout(client)


def test_token_revocation(client: TestClient):
    """Token 撤销后立即失效。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # 创建 Agent + Grant + Token
    r = client.post("/agent-access/clients", json={"display_name": "Revoked Agent"})
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read"],
    })
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # 绑定渠道
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_test2"},
    )

    # Token 有效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # 撤销 Token
    r = client.get(f"/agent-access/tokens/{grant_id}")
    token_id = r.json()[0]["id"]
    r = client.delete(f"/agent-access/tokens/{token_id}")
    assert r.status_code == 200

    # Token 失效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, f"撤销的 Token 应返回 401: {r.status_code}"

    _logout(client)


def test_grant_revocation(client: TestClient):
    """Grant 撤销后所有关联 Token 失效。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # 创建 Agent + Grant + Token
    r = client.post("/agent-access/clients", json={"display_name": "Grant Revoke Agent"})
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read"],
    })
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # 绑定渠道
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_test3"},
    )

    # Token 有效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # 撤销 Grant
    r = client.delete(f"/agent-access/grants/{grant_id}")
    assert r.status_code == 200

    # Token 失效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401

    _logout(client)


def test_non_owner_cannot_access_agent_management(client: TestClient):
    """非 owner 不能访问 Agent 管理端点。"""
    _ensure_owner(client)
    _init_owner_password(client)
    _logout(client)  # init-password 会自动登录，先退出

    # 不登录直接访问
    r = client.get("/agent-access/clients")
    assert r.status_code == 401

    r = client.post("/agent-access/clients", json={"display_name": "Hacker"})
    assert r.status_code == 401


def test_invalid_token_rejected(client: TestClient):
    """无效 Token 被拒绝。"""
    # 确保有绑定
    member_id = _ensure_owner(client)
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_test4"},
    )

    # 随机 Token
    r = client.get("/api/v1/books", headers={"Authorization": "Bearer hbs_at_invalid_token_here"})
    assert r.status_code == 401

    # 非 hbs_at 前缀
    r = client.get("/api/v1/books", headers={"Authorization": "Bearer random-string"})
    assert r.status_code == 401


def test_token_not_logged(client: TestClient):
    """Token 不应出现在日志中（验证返回结构不含明文 hash）。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    r = client.post("/agent-access/clients", json={"display_name": "Log Test Agent"})
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read"],
    })
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # 列出 token 时不应有明文
    r = client.get(f"/agent-access/tokens/{grant_id}")
    tokens = r.json()
    assert len(tokens) > 0
    for t in tokens:
        assert "token" not in t  # 列表中不含明文
        assert "token_hash" not in t  # 也不含 hash
        assert "token_prefix" in t  # 只有前缀

    _logout(client)


def test_scope_enforcement(client: TestClient):
    """Scope 限制：只有 books:read 的 Token 不能写入。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # 绑定渠道
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_scope_test"},
    )

    # 创建只有 books:read scope 的 Grant
    r = client.post("/agent-access/clients", json={"display_name": "Read Only Agent"})
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read"],  # 只有读权限
    })
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # 读取应该成功
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # 写入应该被拒绝（只有 books:read，没有 books:write）
    r = client.post("/api/v1/books", json={"title": "scope test"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, f"只有 books:read 的 Token 不应能写入: {r.status_code} {r.text}"

    _logout(client)


def test_expired_token_rejected(client: TestClient):
    """过期的 Grant/Token 被拒绝。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # 绑定渠道
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_expired"},
    )

    # 创建一个 1 天过期的 Grant
    r = client.post("/agent-access/clients", json={"display_name": "Expiring Agent"})
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read"],
        "expires_in_days": 1,
    })
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # Token 当前有效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # 手动过期 Grant
    from app.models import AgentGrant
    from app.db import SessionLocal
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    grant = db.get(AgentGrant, grant_id)
    grant.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    db.commit()
    db.close()

    # Token 现在应该无效
    r = client.get("/api/v1/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, f"过期的 Token 应返回 401: {r.status_code}"

    _logout(client)


def test_wrong_member_rejected(client: TestClient):
    """Agent Token 的 member_id 与请求的 member_id 不一致时被拒绝。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # 创建并绑定渠道
    headers = {"X-Channel": "feishu", "X-External-User-Id": "ou_owner"}
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_owner"},
    )
    r = client.post("/api/v1/members", json={"name": "other", "role": "member"}, headers=headers)
    other_member_id = r.json()["data"]["id"]

    # 创建绑定到 owner 的 Agent Token（含 books:write 和 notes:write）
    r = client.post("/agent-access/clients", json={"display_name": "Owner Agent"})
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:write", "notes:write"],
    })
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # 用 Agent Token 创建一本书
    r = client.post(
        "/api/v1/books",
        json={"title": "wrong member test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    book_id = r.json()["data"]["id"]

    # 用 owner 的 Token 但指定 other_member_id 创建笔记应被拒绝
    r = client.post(
        f"/api/v1/books/{book_id}/notes",
        json={"member_id": other_member_id, "content_md": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, f"Agent Token 不能操作其他成员的数据: {r.status_code} {r.text}"

    _logout(client)


def test_init_password_upgrade_protected(client: TestClient):
    """CHK-039-P1：Owner 已存在但密码未初始化时，无 SETUP_TOKEN 的非本机请求应被拒绝。"""
    _ensure_owner(client)

    # 不带 X-Setup-Token 且非 loopback -> 403
    r = client.post(
        "/auth/init-password",
        json={"password": "hijack-123", "confirm": "hijack-123"},
    )
    assert r.status_code == 403, f"非本机无 token 应被拒绝: {r.status_code} {r.text}"

    # 带错误的 token -> 403
    r = client.post(
        "/auth/init-password",
        json={"password": "hijack-123", "confirm": "hijack-123"},
        headers={"X-Setup-Token": "wrong-token"},
    )
    assert r.status_code == 403

    # 带正确的 token -> 200
    from app.config import settings

    r = client.post(
        "/auth/init-password",
        json={"password": "correct-123", "confirm": "correct-123"},
        headers={"X-Setup-Token": settings.setup_token},
    )
    assert r.status_code == 200, r.text


def test_introspect_endpoint(client: TestClient):
    """CHK-039-P2：/auth/introspect 能验证 Bearer Token 并返回 client 信息。"""
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)

    # 创建 Agent Token
    r = client.post("/agent-access/clients", json={"display_name": "Introspect Agent"})
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read", "books:write"],
    })
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    token = r.json()["token"]

    # 绑定渠道（Agent Token 写入需要绑定）
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_intro"},
    )

    # introspect 应返回有效信息
    r = client.get("/auth/introspect", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["active"] is True
    assert data["member_id"] == member_id
    assert "books:read" in data["scopes"]
    assert "books:write" in data["scopes"]
    assert data["client_name"] == "Introspect Agent"

    # 无效 Token -> 401
    r = client.get("/auth/introspect", headers={"Authorization": "Bearer hbs_at_invalid"})
    assert r.status_code == 401

    # 无 Authorization header -> 401
    r = client.get("/auth/introspect")
    assert r.status_code == 401

    _logout(client)
