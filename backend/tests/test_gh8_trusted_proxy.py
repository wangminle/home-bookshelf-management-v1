"""GitHub #8 回归：反代场景下 init-password 的 loopback 检测。

- 直接对端是可信代理（TRUSTED_PROXIES）时，以 X-Forwarded-For 首跳判定 loopback；
- 未配置可信代理时，XFF 一律不可信（防伪造绕过）。
"""

from __future__ import annotations

from starlette.requests import Request

from app.api.v1.web_auth import _is_loopback_request
from app.config import settings


def _make_request(client_host: str, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/init-password",
        "query_string": b"",
        "client": (client_host, 50000),
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
        ],
    }
    return Request(scope)


def test_direct_loopback_is_loopback(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert _is_loopback_request(_make_request("127.0.0.1")) is True


def test_gateway_peer_not_loopback_by_default(monkeypatch):
    """lwa 场景：对端是 Docker 网关 IP；未配置 TRUSTED_PROXIES 时判非 loopback。"""
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert _is_loopback_request(_make_request("172.18.0.1")) is False


def test_xff_not_trusted_without_proxy_config(monkeypatch):
    """未配置可信代理时，伪造 XFF: 127.0.0.1 不能绕过判定（防滥用）。"""
    monkeypatch.setattr(settings, "trusted_proxies", "")
    req = _make_request("172.18.0.1", {"X-Forwarded-For": "127.0.0.1"})
    assert _is_loopback_request(req) is False


def test_trusted_proxy_uses_xff_rightmost_non_trusted(monkeypatch):
    """对端在 TRUSTED_PROXIES 内：右起首个非可信地址是 127.0.0.1 -> loopback。

    BUG-181（GitHub #10）：右值法。本例中 172.18.0.1 是网关亲手追加的自身地址
    （可信，跳过），127.0.0.1 是网关写入的真实客户端地址。
    """
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    req = _make_request("172.18.0.1", {"X-Forwarded-For": "127.0.0.1, 172.18.0.1"})
    assert _is_loopback_request(req) is True


def test_spoofed_xff_first_hop_rejected(monkeypatch):
    """BUG-181 回归：客户端自带伪造 XFF 首跳不能通过 loopback 判定。

    场景：LAN 攻击者（192.168.0.22）发送 X-Forwarded-For: 127.0.0.1，
    nginx 默认 $proxy_add_x_forwarded_for 追加其真实 IP -> XFF 为
    "127.0.0.1, 192.168.0.22"。右值法取 192.168.0.22（非可信、非 loopback）。
    """
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    req = _make_request("172.18.0.1", {"X-Forwarded-For": "127.0.0.1, 192.168.0.22"})
    assert _is_loopback_request(req) is False


def test_multi_hop_xff_skips_trusted_chain(monkeypatch):
    """多级代理链：从右跳过所有可信代理后取第一个非可信地址。

    XFF "127.0.0.1, 192.168.0.22, 172.18.0.5"：右起 172.18.0.5 可信跳过，
    192.168.0.22 非可信即停（首跳的 127.0.0.1 无论真假都不再看）。
    """
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    req = _make_request("172.18.0.1", {"X-Forwarded-For": "127.0.0.1, 192.168.0.22, 172.18.0.5"})
    assert _is_loopback_request(req) is False


def test_trusted_proxy_xff_lan_client_not_loopback(monkeypatch):
    """对端可信但真实客户端是 LAN 地址 → 非 loopback。"""
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    req = _make_request("172.18.0.1", {"X-Forwarded-For": "192.168.0.22"})
    assert _is_loopback_request(req) is False


def test_trusted_proxy_without_xff_falls_back_to_peer(monkeypatch):
    """可信代理但没传 XFF → 以对端地址判定（网关 IP 非 loopback）。"""
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    assert _is_loopback_request(_make_request("172.18.0.1")) is False


def test_untrusted_peer_ignores_xff_even_if_configured(monkeypatch):
    """配置了可信代理，但对端不在列表内 → XFF 不可信。"""
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    req = _make_request("172.18.0.1", {"X-Forwarded-For": "127.0.0.1"})
    assert _is_loopback_request(req) is False
