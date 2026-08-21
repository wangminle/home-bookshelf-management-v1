"""可信网络判定（权限阶段 1，基线 §2.1/§11.3）。

C 模式的"局域网匿名共享"只在可验证来源位于可信网络时开放：
- Loopback only 档：127.0.0.1/::1 始终可信（本机开发）；
- 家庭 LAN 档：直连对端位于 TRUSTED_LAN_CIDRS；
- 反代/网关档：对端为 TRUSTED_PROXIES 内的可信代理时，按右值法
  解析 X-Forwarded-For（与 web_auth._client_ip_behind_proxy 同口径，
  BUG-181：从右向左跳过可信代理取首个非可信地址，防伪造首跳），
  还原出的真实客户端位于回环或可信 LAN 才可信。

不无条件信任 X-Forwarded-For/Forwarded/Host 等客户端可伪造头（基线 §11.3）。
无法确认来源时返回不可信，由调用方执行 L1 降级（只保留 L0/登录入口）。
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class TrustDecision:
    trusted: bool
    client_ip: str | None  # 还原出的真实客户端 IP（直连时等于对端）
    reason: str


_UNTRUSTED_PEER = "untrusted_peer"
_UNTRUSTED_CLIENT = "untrusted_client"
_XFF_UNTRUSTED_PEER = "xff_from_untrusted_peer"
_NOT_AN_IP = "not_an_ip"


def _parse_ip(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not host:
        return None
    try:
        return ipaddress.ip_address(host.strip())
    except ValueError:
        return None


def _in_networks(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    return any(addr in net for net in networks)


def _is_trusted_proxy(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    proxy_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    return _in_networks(addr, proxy_networks)


def resolve_client_ip_behind_proxy(
    xff: str | None,
    proxy_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str | None:
    """右值法还原真实客户端 IP：从右向左跳过可信代理，取首个非可信地址。

    全部跳点均可信（纯网关链）时返回 None——发起方是可信网关自身。
    """
    if not xff:
        return None
    for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
        addr = _parse_ip(hop)
        if addr is None:
            return hop  # 无法解析的跳点不可信，按其原样返回（判定为不可信客户端）
        if not _is_trusted_proxy(addr, proxy_networks):
            return hop
    return None


def evaluate_trust(
    peer_host: str | None,
    xff: str | None,
    *,
    trusted_lan_networks: list,
    trusted_proxy_networks: list,
) -> TrustDecision:
    """判定请求来源是否可信局域网。

    判定顺序（CHK-071 P0 修复）：
    1. 对端是可信代理 → 必须解析 XFF 还原真实客户端再判定；
    2. 携带 X-Forwarded-For 但对端不是可信代理 → 不可信（无法验证的转发链）；
    3. 无 XFF 的直连请求才按回环 / 可信 LAN CIDR 判定。

    此前"先按回环/LAN 放行、再看代理"的顺序会让本机 nginx（对端 127.0.0.1
    携公网 XFF）或同时位于 LAN 网段与 TRUSTED_PROXIES 的网关绕过边界。
    """
    peer = _parse_ip(peer_host)
    if peer is None:
        return TrustDecision(False, peer_host, _NOT_AN_IP)

    # 1. 可信代理：XFF 右值法还原真实客户端
    if _is_trusted_proxy(peer, trusted_proxy_networks):
        client = resolve_client_ip_behind_proxy(xff, trusted_proxy_networks)
        if client is None:
            # CHK-073/BUG-200：可信代理但无可解析 XFF——最终客户端来源不明，
            # 对承载真实数据的匿名门控 fail-closed（网关探活请用 /public-health
            # 等独立端点，不复用书目访问门控）
            return TrustDecision(False, str(peer), "proxy_without_xff")
        client_addr = _parse_ip(client)
        if client_addr is not None:
            if client_addr.is_loopback:
                return TrustDecision(True, str(client_addr), "proxy_loopback")
            if _in_networks(client_addr, trusted_lan_networks):
                return TrustDecision(True, str(client_addr), "proxy_lan_cidr")
        return TrustDecision(False, client, _UNTRUSTED_CLIENT)

    # 2. 携带 XFF 的非代理对端：转发链不可验证，一律拒绝
    if xff and xff.strip():
        return TrustDecision(False, str(peer), "xff_from_untrusted_peer")

    # 3. 直连：回环或可信 LAN
    if peer.is_loopback:
        return TrustDecision(True, str(peer), "loopback")
    if _in_networks(peer, trusted_lan_networks):
        return TrustDecision(True, str(peer), "lan_cidr")

    return TrustDecision(False, str(peer), _UNTRUSTED_PEER)


def evaluate_request_trust(request) -> TrustDecision:
    """FastAPI Request 快捷入口：读取对端与 XFF 并判定。"""
    from app.config import settings

    peer = request.client.host if request.client else None
    xff = request.headers.get("x-forwarded-for")
    return evaluate_trust(
        peer,
        xff,
        trusted_lan_networks=settings.trusted_lan_networks,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
