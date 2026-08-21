"""共享限流服务与可信网络判定单元测试（权限阶段 1）。"""
from __future__ import annotations

import ipaddress

import pytest

from app.services import rate_limit, trusted_network


# ── rate_limit ──


def test_rate_limit_allows_until_limit() -> None:
    rate_limit.reset()
    decisions = [rate_limit.check("t:a", limit=3, window_seconds=60) for _ in range(3)]
    assert all(d.allowed for d in decisions)
    assert [d.remaining for d in decisions] == [2, 1, 0]


def test_rate_limit_rejects_over_limit_with_retry_after() -> None:
    rate_limit.reset()
    for _ in range(2):
        rate_limit.check("t:b", limit=2, window_seconds=60)
    d = rate_limit.check("t:b", limit=2, window_seconds=60)
    assert not d.allowed
    assert d.retry_after_seconds >= 1
    assert d.remaining == 0
    # 拒绝后不重复计数：窗口过期前持续拒绝
    assert not rate_limit.check("t:b", limit=2, window_seconds=60).allowed


def test_rate_limit_keys_are_isolated() -> None:
    rate_limit.reset()
    for _ in range(5):
        rate_limit.check("t:c1", limit=5, window_seconds=60)
    assert not rate_limit.check("t:c1", limit=5, window_seconds=60).allowed
    assert rate_limit.check("t:c2", limit=5, window_seconds=60).allowed


def test_rate_limit_window_rolls(monkeypatch) -> None:
    rate_limit.reset()
    base = 1_000.0
    clock = {"now": base}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["now"])
    assert rate_limit.check("t:d", limit=1, window_seconds=60).allowed
    assert not rate_limit.check("t:d", limit=1, window_seconds=60).allowed
    clock["now"] = base + 61  # 窗口过期
    assert rate_limit.check("t:d", limit=1, window_seconds=60).allowed


# ── trusted_network ──


LAN = [ipaddress.ip_network("192.168.1.0/24")]
PROXIES = [ipaddress.ip_network("172.18.0.0/16")]


def test_loopback_always_trusted() -> None:
    d = trusted_network.evaluate_trust("127.0.0.1", None, trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES)
    assert d.trusted and d.reason == "loopback"
    d6 = trusted_network.evaluate_trust("::1", None, trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES)
    assert d6.trusted


def test_direct_lan_peer_trusted() -> None:
    d = trusted_network.evaluate_trust("192.168.1.23", None, trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES)
    assert d.trusted and d.reason == "lan_cidr"
    assert d.client_ip == "192.168.1.23"


def test_direct_foreign_peer_untrusted() -> None:
    d = trusted_network.evaluate_trust("8.8.8.8", None, trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES)
    assert not d.trusted and d.reason == "untrusted_peer"


def test_private_but_unconfigured_network_untrusted() -> None:
    """10.x 未配置进 TRUSTED_LAN_CIDRS 时不可信——不默认信任任意私网。"""
    d = trusted_network.evaluate_trust("10.0.0.9", None, trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES)
    assert not d.trusted


def test_trusted_proxy_lan_client_trusted() -> None:
    """对端是可信代理且 XFF 还原出的客户端在可信 LAN → 可信。"""
    d = trusted_network.evaluate_trust(
        "172.18.0.5", "192.168.1.50, 172.18.0.5",
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert d.trusted and d.reason == "proxy_lan_cidr"
    assert d.client_ip == "192.168.1.50"


def test_trusted_proxy_forged_first_hop_not_trusted() -> None:
    """BUG-181 口径：客户端自带伪造首跳（在 LAN 内）+ 网关追加真实地址（公网）→ 不可信。

    XFF = "192.168.1.50, 8.8.8.8, 172.18.0.5"（右侧网关追加）：
    从右向左跳过可信代理 172.18.0.5，第一个非可信地址是 8.8.8.8 → 拒绝。
    """
    d = trusted_network.evaluate_trust(
        "172.18.0.5", "192.168.1.50, 8.8.8.8, 172.18.0.5",
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert not d.trusted
    assert d.client_ip == "8.8.8.8"


def test_untrusted_peer_xff_ignored() -> None:
    """对端不是可信代理时，XFF 一律不可信（防伪造）。"""
    d = trusted_network.evaluate_trust(
        "203.0.113.9", "192.168.1.50, 203.0.113.9",
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert not d.trusted


def test_trusted_proxy_without_xff_fails_closed() -> None:
    """CHK-073/BUG-200：可信代理无可解析 XFF → 来源不明，fail-closed。

    网关探活请用 /public-health 等独立端点，不复用书目访问门控。
    """
    d = trusted_network.evaluate_trust(
        "172.18.0.5", None,
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert not d.trusted and d.reason == "proxy_without_xff"
    # XFF 全部跳点均为可信代理（无原始客户端信息）同样拒绝
    d2 = trusted_network.evaluate_trust(
        "172.18.0.5", "172.18.0.5",
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert not d2.trusted


def test_non_ip_peer_untrusted() -> None:
    d = trusted_network.evaluate_trust("testclient", None, trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES)
    assert not d.trusted and d.reason == "not_an_ip"


def test_empty_lan_config_still_allows_loopback() -> None:
    """未配置 TRUSTED_LAN_CIDRS：仅回环可信（Loopback only 档）。"""
    d = trusted_network.evaluate_trust("192.168.1.23", None, trusted_lan_networks=[], trusted_proxy_networks=[])
    assert not d.trusted
    assert trusted_network.evaluate_trust("127.0.0.1", None, trusted_lan_networks=[], trusted_proxy_networks=[]).trusted


# ── CHK-071 P0 修复：可信代理优先判定，堵住回环/LAN 直连绕过 ──


def test_loopback_peer_with_public_xff_rejected() -> None:
    """本机 nginx（对端 127.0.0.1，未配 TRUSTED_PROXIES）携公网 XFF → 拒绝。

    旧实现先按回环放行、不看 XFF，导致本地反代转发公网流量时绕过局域网边界。
    """
    d = trusted_network.evaluate_trust(
        "127.0.0.1", "203.0.113.9",
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert not d.trusted
    assert d.reason == "xff_from_untrusted_peer"


def test_lan_peer_with_public_xff_rejected() -> None:
    """LAN 直连客户端（不在 TRUSTED_PROXIES）携 XFF → 无法验证的转发链，拒绝。"""
    d = trusted_network.evaluate_trust(
        "192.168.1.30", "8.8.8.8",
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert not d.trusted
    assert d.reason == "xff_from_untrusted_peer"


def test_gateway_in_both_lan_and_proxies_resolves_xff_first() -> None:
    """网关同时配置在 TRUSTED_LAN_CIDRS 与 TRUSTED_PROXIES：必须先走代理分支解析 XFF。

    旧实现 lan_cidr 直连放行忽略了公网 XFF；新顺序代理优先 → 解析出公网客户端拒绝。
    """
    overlapping_proxies = [ipaddress.ip_network("192.168.1.0/24")]
    d = trusted_network.evaluate_trust(
        "192.168.1.1", "203.0.113.9, 192.168.1.1",
        trusted_lan_networks=LAN, trusted_proxy_networks=overlapping_proxies,
    )
    assert not d.trusted
    assert d.client_ip == "203.0.113.9"


def test_direct_lan_client_without_xff_still_trusted() -> None:
    """回归：普通 LAN 直连浏览器（无 XFF）不受影响。"""
    d = trusted_network.evaluate_trust(
        "192.168.1.30", None,
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert d.trusted and d.reason == "lan_cidr"


def test_loopback_without_xff_still_trusted() -> None:
    d = trusted_network.evaluate_trust(
        "127.0.0.1", None,
        trusted_lan_networks=LAN, trusted_proxy_networks=PROXIES,
    )
    assert d.trusted and d.reason == "loopback"


def test_rate_limit_mixed_windows_do_not_reset_each_other(monkeypatch) -> None:
    """CHK-071：不同 Profile 窗口互不干扰——短窗口请求不得提前清掉长窗口配额。"""
    rate_limit.reset()
    base = 2_000.0
    clock = {"now": base}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["now"])
    # 长窗口 bucket：60s / 1 次
    assert rate_limit.check("mix:long", limit=1, window_seconds=60).allowed
    # 短窗口 bucket：5s / 1 次，过期重建触发清理路径
    assert rate_limit.check("mix:short", limit=1, window_seconds=5).allowed
    clock["now"] = base + 6  # 短窗口过期；若清理误用调用方窗口，长窗口会被一并清掉
    assert rate_limit.check("mix:short", limit=1, window_seconds=5).allowed
    # 长窗口仍在有效期内：未恢复配额
    assert not rate_limit.check("mix:long", limit=1, window_seconds=60).allowed
    clock["now"] = base + 61
    assert rate_limit.check("mix:long", limit=1, window_seconds=60).allowed
    rate_limit.reset()
