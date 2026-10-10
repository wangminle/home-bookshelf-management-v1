"""BUG-311~317 回归：代码审查批次二的安全修复。

- BUG-311 匿名 bind 收紧：纯密码部署下匿名仅空库引导期放行（对齐 BUG-221）
- BUG-312 角色变更吊销该成员全部 Agent Token（此前只撤 Web 会话）
- BUG-313 CLI owner-reset-password 重置后吊销会话与 Agent Token
- BUG-314 agent-access 写端点补齐同源 CSRF 校验
- BUG-317 X-Forwarded-Proto 仅在直接对端为可信代理时采信（Secure Cookie 判定）

BUG-315（update_candidate 拒绝空 photo_ids）与 BUG-316（删除/更新幂等摘要
含期望版本）的回归就近放在 test_intake_workflow.py / test_storage_photos.py。
"""
from __future__ import annotations

import getpass

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

import app.admin as admin_mod
from app.api.v1.web_auth import _is_secure_request
from app.config import settings
from app.main import app
from app.models import Member
from app.services import agent_access

# conftest 固定 SETUP_TOKEN=test-setup-token（CHK-039）
_SETUP_TOKEN = "test-setup-token"


# ── BUG-311：匿名 bind ──

def test_bug311_anonymous_bind_rejected_once_members_exist(
        client: TestClient, anon_client: TestClient):
    """已有成员后匿名 bind 一律 403。

    旧逻辑附带的"系统尚无渠道绑定"代理条件在纯密码部署下恒真，匿名可绑定
    任意成员冒充 Owner；现对齐 add_member 的 BUG-221 口径：仅空库引导期放行。
    """
    m = client.post("/api/v1/members", json={"name": "甲", "role": "member"})
    assert m.status_code == 201, m.text
    member_id = m.json()["data"]["id"]

    r = anon_client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu",
              "external_user_id": "ou_attacker"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_attacker"},
    )
    assert r.status_code == 403, r.text


def test_bug311_setup_token_still_allows_bind(
        client: TestClient, anon_client: TestClient):
    """初始化通道保留：持有正确 X-Setup-Token 的 bind 仍放行（CHK-039 场景）。"""
    m = client.post("/api/v1/members", json={"name": "乙", "role": "member"})
    assert m.status_code == 201, m.text
    member_id = m.json()["data"]["id"]

    r = anon_client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu",
              "external_user_id": "ou_setup"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_setup",
                 "X-Setup-Token": _SETUP_TOKEN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["channel_bindings"]["feishu"] == "ou_setup"


def test_bug311_empty_db_bootstrap_bind_still_works(anon_client: TestClient):
    """空库引导不变：首个匿名 bind（member_id=1）仍自动创建默认 owner 并绑定。"""
    r = anon_client.post(
        "/api/v1/members/bind",
        json={"member_id": 1, "channel": "feishu", "external_user_id": "ou_first"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_first"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["role"] == "owner"


# ── BUG-312：角色变更 / 密码重置吊销 Agent Token ──

def _issue_agent_token(client: TestClient, member_id: int) -> str:
    """经管理 API 为成员签发一个 Agent Token（返回明文）。"""
    r = client.post("/agent-access/clients", json={"display_name": "回归客户端"})
    assert r.status_code == 200, r.text
    agent_client_id = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": ["books:read"],
    })
    assert r.status_code == 200, r.text
    grant_id = r.json()["id"]
    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_bug312_role_change_revokes_agent_tokens(
        client: TestClient, db_session: Session):
    """角色变更同时吊销该成员全部 Agent Token——此前只撤 Web 会话，
    降权成员仍持旧授权 Token 继续以原 scope 操作（基线 §11.2）。"""
    m = client.post("/api/v1/members", json={"name": "成员乙", "role": "member"})
    assert m.status_code == 201, m.text
    member_id = m.json()["data"]["id"]

    token = _issue_agent_token(client, member_id)
    assert agent_access.verify_token(db_session, token) is not None

    r = client.patch(f"/api/v1/members/{member_id}", json={"role": "owner"})
    assert r.status_code == 200, r.text
    assert agent_access.verify_token(db_session, token) is None


def test_bug312_password_reset_revokes_agent_tokens(
        client: TestClient, db_session: Session):
    """密码重置同样吊销 Agent Token 与 Web 会话
    （BUG-222 内联块抽取为 revoke_member_agent_tokens，与角色变更共用）。"""
    m = client.post("/api/v1/members", json={"name": "成员丙", "role": "member"})
    assert m.status_code == 201, m.text
    member_id = m.json()["data"]["id"]

    token = _issue_agent_token(client, member_id)
    web_token, _ = agent_access.create_web_session(db_session, member_id)
    assert agent_access.verify_token(db_session, token) is not None
    assert agent_access.verify_web_session(db_session, web_token) is not None

    r = client.post(f"/api/v1/members/{member_id}/password",
                    json={"password": "new-pass-123456"})
    assert r.status_code == 200, r.text
    assert agent_access.verify_token(db_session, token) is None
    assert agent_access.verify_web_session(db_session, web_token) is None


# ── BUG-313：CLI owner-reset-password 吊销会话与 Agent Token ──

def test_bug313_cli_owner_reset_revokes_sessions_and_tokens(
        db_engine, monkeypatch, capsys):
    """CLI 重置与 Web 端（BUG-222）口径一致——重置后吊销该 Owner 的全部
    Web 会话与 Agent Token；旧版只改密码且提示"旧会话不会自动失效"。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with SessionLocal() as s:
        owner = Member(name="CLI Owner", role="owner")
        s.add(owner)
        s.commit()
        owner_id = owner.id
        agent_access.set_member_password(s, owner, "old-password-123")
        web_token, _ = agent_access.create_web_session(s, owner_id)
        agent_client = agent_access.register_agent_client(
            s, display_name="CLI 回归客户端")
        s.commit()
        grant = agent_access.create_grant(
            s, agent_client_id=agent_client.id, member_id=owner_id,
            scopes=["books:read"], expires_in_days=30,
            approved_by_member_id=owner_id)
        s.commit()
        agent_token, _ = agent_access.issue_token(s, grant.id)
        s.commit()

    # CLI 走导入时绑定的 SessionLocal，须替换为本用例库
    monkeypatch.setattr(admin_mod, "SessionLocal", SessionLocal)
    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    monkeypatch.setattr(getpass, "getpass", lambda *_: "new-password-456")
    admin_mod.cmd_owner_reset_password()

    out = capsys.readouterr().out
    assert "已重置" in out
    assert "已吊销" in out

    with SessionLocal() as s:
        assert agent_access.verify_web_session(s, web_token) is None
        assert agent_access.verify_token(s, agent_token) is None
        owner = s.get(Member, owner_id)
        assert agent_access.verify_member_password(s, owner, "new-password-456")


# ── BUG-314：agent-access 写端点同源 CSRF ──

def test_bug314_agent_access_write_requires_origin(client: TestClient):
    """写端点补齐 verify_csrf（defense-in-depth）：已登录会话但无
    Origin/Referer 的写请求应 403；读端点不受影响。"""
    c2 = TestClient(app)
    c2.cookies.set("hbs_session", client.cookies.get("hbs_session"),
                   domain="testserver.local")

    r = c2.post("/agent-access/clients", json={"display_name": "无源请求"})
    assert r.status_code == 403, r.text
    assert "CSRF" in r.json()["detail"]

    r = c2.get("/agent-access/clients")
    assert r.status_code == 200, r.text


# ── BUG-317：X-Forwarded-Proto 仅可信代理采信 ──

def _make_request(client_host: str, headers: dict[str, str] | None = None,
                  *, scheme: str = "http") -> Request:
    # 真实 ASGI 请求必有 Host 头；缺了它 starlette 的 request.url 会退化为
    # 纯 path，scheme 解析不出来（影响 _is_secure_request 的直连回退分支）
    raw = {"host": "bookshelf.example"}
    raw.update(headers or {})
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "query_string": b"",
        "scheme": scheme,
        "client": (client_host, 50000),
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in raw.items()
        ],
    }
    return Request(scope)


def test_bug317_trusted_proxy_xfp_https_is_secure(monkeypatch):
    """对端是可信代理时采信 X-Forwarded-Proto（lwa/nginx 反代场景）。"""
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    req = _make_request("10.0.0.5", {"X-Forwarded-Proto": "https"})
    assert _is_secure_request(req) is True


def test_bug317_trusted_proxy_xfp_http_not_secure(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    req = _make_request("10.0.0.5", {"X-Forwarded-Proto": "http"})
    assert _is_secure_request(req) is False


def test_bug317_untrusted_peer_xfp_ignored(monkeypatch):
    """旧版缺陷回归：任意客户端自带 X-Forwarded-Proto: https 不能干扰
    Secure 判定（与 XFF 的 BUG-179/181 右值法同口径）。"""
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    req = _make_request("192.168.1.7", {"X-Forwarded-Proto": "https"})
    assert _is_secure_request(req) is False


def test_bug317_trusted_proxy_without_xfp_falls_back_to_scheme(monkeypatch):
    """可信代理但未声明 X-Forwarded-Proto → 以实际 scheme 判定。"""
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    assert _is_secure_request(_make_request("10.0.0.5")) is False


def test_bug317_direct_https_is_secure(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert _is_secure_request(_make_request("203.0.113.9", scheme="https")) is True
