"""WBS-7：Agent 访问管理测试。

验证：
1. 只有 owner 可创建/管理 Grant
2. Token 只显示一次
3. Grant 生命周期（创建/修改/撤销）
4. 高风险 Scope 不通过通配符
5. 列表中不泄露明文 Token
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


def _setup_owner(client: TestClient) -> int:
    member_id = _ensure_owner(client)
    _init_owner_password(client)
    _login(client)
    return member_id


class TestOwnerOnlyAccess:

    def test_anonymous_cannot_list_clients(self, client: TestClient):
        """匿名不能列出 Agent 客户端。"""
        r = client.get("/agent-access/clients")
        assert r.status_code == 401

    def test_anonymous_cannot_create_client(self, client: TestClient):
        """匿名不能创建 Agent 客户端。"""
        r = client.post("/agent-access/clients", json={"display_name": "hacker"})
        assert r.status_code == 401

    def test_anonymous_cannot_list_grants(self, client: TestClient):
        """匿名不能列出授权。"""
        r = client.get("/agent-access/grants")
        assert r.status_code == 401

    def test_anonymous_cannot_issue_token(self, client: TestClient):
        """匿名不能签发令牌。"""
        r = client.post("/agent-access/tokens", json={"grant_id": 1})
        assert r.status_code == 401


class TestTokenOneTimeDisplay:

    def test_token_only_in_create_response(self, client: TestClient):
        """Token 明文只出现在创建响应中。"""
        member_id = _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Token Test"})
        agent_client_id = r.json()["id"]

        r = client.post("/agent-access/grants", json={
            "agent_client_id": agent_client_id,
            "member_id": member_id,
            "scopes": ["books:read"],
        })
        grant_id = r.json()["id"]

        # 签发 Token - 明文出现在这里
        r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
        assert r.status_code == 200
        token = r.json()["token"]
        assert token.startswith("hbs_at_")

        # 列出 Token - 不应有明文
        r = client.get(f"/agent-access/tokens/{grant_id}")
        tokens = r.json()
        assert len(tokens) > 0
        for t in tokens:
            assert "token" not in t, "Token 列表中泄露明文"
            assert "token_hash" not in t, "Token 列表中泄露 hash"
            assert "token_prefix" in t, "Token 列表应包含前缀"

        _logout(client)


class TestGrantLifecycle:

    def test_create_grant_with_expiry(self, client: TestClient):
        """创建带有效期的 Grant。"""
        member_id = _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Expiry Agent"})
        agent_client_id = r.json()["id"]

        r = client.post("/agent-access/grants", json={
            "agent_client_id": agent_client_id,
            "member_id": member_id,
            "scopes": ["books:read"],
            "expires_in_days": 7,
        })
        assert r.status_code == 200
        grant = r.json()
        assert grant["status"] == "active"
        assert grant["expires_at"] is not None
        assert "books:read" in grant["scopes"]

        _logout(client)

    def test_update_grant_scopes(self, client: TestClient):
        """修改 Grant 的 scopes。"""
        member_id = _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Update Agent"})
        agent_client_id = r.json()["id"]

        r = client.post("/agent-access/grants", json={
            "agent_client_id": agent_client_id,
            "member_id": member_id,
            "scopes": ["books:read"],
        })
        grant_id = r.json()["id"]

        # 修改 scopes
        r = client.patch(f"/agent-access/grants/{grant_id}", json={
            "scopes": ["books:read", "books:write"],
        })
        assert r.status_code == 200
        assert "books:write" in r.json()["scopes"]

        _logout(client)

    def test_revoke_grant(self, client: TestClient):
        """撤销 Grant。"""
        member_id = _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Revoke Agent"})
        agent_client_id = r.json()["id"]

        r = client.post("/agent-access/grants", json={
            "agent_client_id": agent_client_id,
            "member_id": member_id,
            "scopes": ["books:read"],
        })
        grant_id = r.json()["id"]

        # 撤销
        r = client.delete(f"/agent-access/grants/{grant_id}")
        assert r.status_code == 200

        # 查看已撤销
        r = client.get(f"/agent-access/grants/{grant_id}")
        assert r.json()["status"] == "revoked"

        _logout(client)

    def test_revoke_client(self, client: TestClient):
        """撤销 Agent 客户端。"""
        _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Revoke Client"})
        client_id = r.json()["id"]

        r = client.delete(f"/agent-access/clients/{client_id}")
        assert r.status_code == 200

        _logout(client)


class TestScopeValidation:

    def test_wildcard_scope_rejected(self, client: TestClient):
        """通配符 scope 被拒绝。"""
        member_id = _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Wildcard Agent"})
        agent_client_id = r.json()["id"]

        r = client.post("/agent-access/grants", json={
            "agent_client_id": agent_client_id,
            "member_id": member_id,
            "scopes": ["*"],
        })
        assert r.status_code in (400, 422), f"通配符 scope 应被拒绝: {r.status_code}"

        _logout(client)

    def test_admin_scope_rejected(self, client: TestClient):
        """admin:* scope 被拒绝。"""
        member_id = _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Admin Agent"})
        agent_client_id = r.json()["id"]

        r = client.post("/agent-access/grants", json={
            "agent_client_id": agent_client_id,
            "member_id": member_id,
            "scopes": ["admin:*"],
        })
        assert r.status_code in (400, 422)

        _logout(client)

    def test_empty_scopes_rejected(self, client: TestClient):
        """空 scope 列表应被拒绝（至少需要一个 scope）。"""
        member_id = _setup_owner(client)

        r = client.post("/agent-access/clients", json={"display_name": "Empty Scope Agent"})
        agent_client_id = r.json()["id"]

        r = client.post("/agent-access/grants", json={
            "agent_client_id": agent_client_id,
            "member_id": member_id,
            "scopes": [],
        })
        assert r.status_code == 422

        _logout(client)


class TestAgentNameValidation:

    def test_plain_text_name_accepted(self, client: TestClient):
        """纯文本 Agent 名称被接受。"""
        _setup_owner(client)
        r = client.post("/agent-access/clients", json={"display_name": "My Book Agent"})
        assert r.status_code == 200
        assert r.json()["display_name"] == "My Book Agent"
        _logout(client)

    def test_html_in_name_rejected(self, client: TestClient):
        """含 HTML 的名称被拒绝。"""
        _setup_owner(client)
        r = client.post("/agent-access/clients", json={
            "display_name": "<script>alert(1)</script>",
        })
        assert r.status_code in (400, 422)
        _logout(client)

    def test_long_name_rejected(self, client: TestClient):
        """超长名称被拒绝（上限 80 字符）。"""
        _setup_owner(client)
        r = client.post("/agent-access/clients", json={
            "display_name": "A" * 81,
        })
        assert r.status_code in (400, 422)
        _logout(client)
