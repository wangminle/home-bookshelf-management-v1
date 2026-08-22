"""权限阶段 0（任务 0.7）：doctor 部署信任态势检查与渠道缩权预览。

基线 §11.3：对"渠道启用但无签名""反代但无 HTTPS/可信代理""明文 HTTP 非回环"
等不一致给出明确警告；§13：非 Owner 渠道缩权前由 doctor 列出受影响绑定。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from bookshelf.doctor import run_doctor


def _client(
    health_data: dict,
    members: list[dict] | None = None,
    base_url: str = "http://127.0.0.1:8000",
) -> MagicMock:
    client = MagicMock()
    client.base_url = base_url
    merged = {
        "status": "available",
        "service": "home-bookshelf",
        "app_version": "0.3.10",
        "frontend_version": "0.3.10",
        "database": "connected",
        **health_data,
    }
    client.health.return_value = {
        "ok": True,
        "_http_status": 200,
        "data": merged,
    }
    if members is None:
        members = []
    client.members.return_value = {"data": {"items": members, "total": len(members)}}
    return client


def _posture(**kw) -> dict:
    base = {
        "channel_signing_configured": True,
        "channel_bindings_present": True,
        "trusted_proxies_configured": False,
        "public_base_url": None,
        "public_url_https": False,
        "anonymous_catalog_mode": "disabled",
        "trusted_lan_configured": False,
    }
    base.update(kw)
    return base


# ── 渠道签名 ──


def test_doctor_warns_channel_bindings_without_signing() -> None:
    report = run_doctor(_client(_posture(channel_signing_configured=False)))
    assert any("CHANNEL_SIGNING_SECRET" in w for w in report.warnings)


def test_doctor_silent_when_channel_signing_configured() -> None:
    report = run_doctor(_client(_posture(channel_signing_configured=True)))
    assert not any("CHANNEL_SIGNING_SECRET" in w for w in report.warnings)


def test_doctor_no_channel_warning_without_bindings() -> None:
    """无渠道绑定时，未配签名不构成"渠道启用但无签名"不一致。"""
    report = run_doctor(_client(_posture(
        channel_signing_configured=False, channel_bindings_present=False,
    )))
    assert not any("CHANNEL_SIGNING_SECRET" in w for w in report.warnings)


# ── 反代 / HTTPS ──


def test_doctor_warns_trusted_proxy_without_https_public_url() -> None:
    report = run_doctor(_client(_posture(
        trusted_proxies_configured=True,
        public_base_url="http://bookshelf.home",
        public_url_https=False,
    )))
    assert any("HTTPS" in w for w in report.warnings)


def test_doctor_warns_trusted_proxy_without_public_url() -> None:
    report = run_doctor(_client(_posture(trusted_proxies_configured=True)))
    assert any("PUBLIC_BASE_URL" in w for w in report.warnings)


def test_doctor_warns_public_http_url_non_loopback() -> None:
    report = run_doctor(_client(_posture(
        public_base_url="http://192.168.1.10:8000", public_url_https=False,
    )))
    assert any("明文 HTTP" in w for w in report.warnings)


def test_doctor_silent_on_https_public_url() -> None:
    report = run_doctor(_client(_posture(
        public_base_url="https://bookshelf.home", public_url_https=True,
    )))
    assert not any("HTTPS" in w for w in report.warnings)
    assert not any("明文 HTTP" in w for w in report.warnings)


def test_doctor_warns_api_url_plain_http_non_loopback() -> None:
    """客户端侧：doctor 连的是非回环明文地址时告警（部署档推断）。"""
    report = run_doctor(_client(_posture(), base_url="http://10.0.0.5:8000"))
    assert any("明文 HTTP" in w for w in report.warnings)


def test_doctor_silent_on_loopback_http() -> None:
    """回环 HTTP 属开发档（基线 §11.3 Loopback only），不告警。"""
    report = run_doctor(_client(_posture(), base_url="http://127.0.0.1:8000"))
    assert not any("明文 HTTP" in w for w in report.warnings)


# ── 渠道缩权预览（基线 §13：发布前列出受影响绑定） ──


def test_doctor_lists_non_owner_channel_bindings_as_narrowed() -> None:
    members = [
        {"id": 1, "name": "爸爸", "role": "owner",
         "channel_bindings": {"feishu": "ou_owner"}},
        {"id": 2, "name": "妈妈", "role": "member",
         "channel_bindings": {"feishu": "ou_member"}},
    ]
    report = run_doctor(_client(_posture(), members=members))
    narrowed = [w for w in report.warnings if "缩权" in w]
    assert len(narrowed) == 1
    assert "妈妈" in narrowed[0]
    assert "books:delete" in narrowed[0]
    assert "stats:household" in narrowed[0]


def test_doctor_no_narrowing_for_owner_only_bindings() -> None:
    members = [
        {"id": 1, "name": "爸爸", "role": "owner",
         "channel_bindings": {"feishu": "ou_owner"}},
    ]
    report = run_doctor(_client(_posture(), members=members))
    assert not any("缩权" in w for w in report.warnings)


# ── 匿名书架（C 模式）配置一致性 ──


def test_doctor_warns_lan_shared_without_trusted_cidrs() -> None:
    report = run_doctor(_client(_posture(
        anonymous_catalog_mode="lan_shared", trusted_lan_configured=False,
    )))
    assert any("TRUSTED_LAN_CIDRS" in w for w in report.warnings)


def test_doctor_silent_when_lan_shared_with_cidrs() -> None:
    report = run_doctor(_client(_posture(
        anonymous_catalog_mode="lan_shared", trusted_lan_configured=True,
    )))
    assert not any("TRUSTED_LAN_CIDRS" in w for w in report.warnings)


def test_doctor_silent_when_catalog_disabled() -> None:
    report = run_doctor(_client(_posture(
        anonymous_catalog_mode="disabled", trusted_lan_configured=False,
    )))
    assert not any("TRUSTED_LAN_CIDRS" in w for w in report.warnings)


# ── 态势不可得（无凭证探活）时跳过 ──


def test_doctor_skips_posture_when_auth_protected() -> None:
    client = MagicMock()
    client.base_url = "http://127.0.0.1:8000"
    client.health.return_value = {
        "ok": True,
        "_http_status": 200,
        "data": {"auth_protected": True, "database": "unknown",
                 "status": "available", "service": "home-bookshelf",
                 "app_version": "0.3.10"},
    }
    client.members.side_effect = RuntimeError("[HTTP 401] unauthorized")
    report = run_doctor(client)
    assert not any("CHANNEL_SIGNING_SECRET" in w for w in report.warnings)
    assert not any("明文 HTTP" in w for w in report.warnings)
