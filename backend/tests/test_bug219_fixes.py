"""BUG-219 修复回归：统一登录区分 0 条与多条凭据。

空库/未初始化密码时 credential 数为 0，resolve_login_member 返回 None 后
login 误报"存在多个登录账号，请提供用户名"。修复后：
- 0 条凭据（未提供用户名）-> 400 提示先初始化密码；
- >1 条凭据（未提供用户名）-> 400 提示提供用户名（原行为保留）；
- 恰 1 条凭据 -> 仍回退到该成员直接校验密码（单账号家庭不受影响）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _create_member_with_password(client: TestClient, name: str) -> int:
    r = client.post("/api/v1/members", json={"name": name, "role": "member"})
    assert r.status_code == 201, r.text
    member_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/members/{member_id}/password", json={"password": "test-pass-12345"})
    assert r.status_code == 200, r.text
    return member_id


def test_login_without_username_zero_credentials_hints_password_init(client: TestClient) -> None:
    """空凭据（夹具默认：owner 存在但未设密码）不再误报"存在多个账号"。"""
    r = client.post("/auth/login", json={"password": "whatever-123"})
    assert r.status_code == 400, r.text
    assert "先初始化密码" in r.json()["detail"]
    assert "存在多个" not in r.json()["detail"]


def test_login_without_username_multiple_credentials_asks_username(client: TestClient) -> None:
    """两条凭据时维持原行为：要求提供用户名。"""
    _create_member_with_password(client, "甲")
    _create_member_with_password(client, "乙")
    r = client.post("/auth/login", json={"password": "whatever-123"})
    assert r.status_code == 400, r.text
    assert "存在多个登录账号" in r.json()["detail"]
    assert "请提供用户名" in r.json()["detail"]


def test_login_without_username_single_credential_fallback(client: TestClient) -> None:
    """恰一条凭据：省略用户名仍回退到该成员，密码正确可登录。"""
    member_id = _create_member_with_password(client, "丙")
    r = client.post("/auth/login", json={"password": "test-pass-12345"})
    assert r.status_code == 200, r.text
    assert r.json()["member_id"] == member_id
