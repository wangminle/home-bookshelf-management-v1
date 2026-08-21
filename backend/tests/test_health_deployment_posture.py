"""权限阶段 0（任务 0.7）：/health 部署信任态势字段。

基线 §11.3：doctor 应能检查渠道签名、绑定、可信代理与 HTTPS 的配置不一致；
/health（受 members:read 保护）下发这些布尔/URL 事实，public-health 不下发。
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_exposes_deployment_posture(client: TestClient, monkeypatch) -> None:
    """/health 返回部署态势五字段（受保护诊断面）。"""
    from app.config import settings
    monkeypatch.setattr(settings, "channel_signing_secret", "unit-test-secret")
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    monkeypatch.setattr(settings, "public_base_url", "https://bookshelf.home")

    r = client.get("/api/v1/health")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["channel_signing_configured"] is True
    assert data["trusted_proxies_configured"] is True
    assert data["public_base_url"] == "https://bookshelf.home"
    assert data["public_url_https"] is True
    assert "channel_bindings_present" in data


def test_health_posture_defaults_when_unconfigured(client: TestClient, monkeypatch) -> None:
    """未配置签名/代理/公开地址时，态势字段如实为 False/None。"""
    from app.config import settings
    monkeypatch.setattr(settings, "channel_signing_secret", None)
    monkeypatch.setattr(settings, "trusted_proxies", "")
    monkeypatch.setattr(settings, "public_base_url", None)

    r = client.get("/api/v1/health")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["channel_signing_configured"] is False
    assert data["trusted_proxies_configured"] is False
    assert data["public_base_url"] is None
    assert data["public_url_https"] is False


def test_health_channel_bindings_present_reflects_db(client: TestClient) -> None:
    """建立渠道绑定后 channel_bindings_present=True。"""
    owner_id = client.get("/auth/session").json()["member_id"]
    r = client.post("/api/v1/members/bind", json={
        "member_id": owner_id, "channel": "feishu", "external_user_id": "ou_posture",
    })
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/health")
    data = r.json()["data"]
    assert data["channel_bindings_present"] is True


def test_public_health_has_no_deployment_posture(client: TestClient) -> None:
    """公开探活不得泄露安全配置态势（仅最小可用性）。"""
    r = client.get("/api/v1/public-health")
    assert r.status_code == 200, r.text
    text = r.text
    for key in ("channel_signing_configured", "trusted_proxies_configured",
                "channel_bindings_present", "public_base_url"):
        assert key not in text, f"public-health 不应暴露 {key}"
