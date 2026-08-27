"""BUG-224 修复回归：verify_csrf 接受真实浏览器 Origin（带端口）。

原实现拿 parsed.netloc（含端口）比对白名单，而默认白名单只有不带端口的
loopback，导致直连 :8000、lwa 网关 :8080、vite 开发态 :3000 等所有真实
部署下 Web Session 写请求全部 403（既有测试用不带端口的 Origin 掩盖了它）。
修复：请求自身 Host 放行（同源定义）+ loopback 任意端口可信。
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _post_book(client: TestClient, origin: str, **headers: str):
    return client.post(
        "/api/v1/books",
        json={"title": "BUG-224 探针", "author": "x", "status": "unread"},
        headers={"Origin": origin, **headers},
    )


def test_csrf_allows_ported_loopback_origin(client: TestClient) -> None:
    """直连部署：Origin 带端口（如 http://127.0.0.1:8000）不再 403。"""
    r = _post_book(client, "http://127.0.0.1:8000")
    assert r.status_code == 201, r.text


def test_csrf_allows_origin_matching_request_host(client: TestClient) -> None:
    """lwa 网关场景：非 loopback 主机，Origin 与请求自身 Host 一致即同源。"""
    r = _post_book(client, "http://10.180.105.86:8080", Host="10.180.105.86:8080")
    assert r.status_code == 201, r.text


def test_csrf_allows_dev_style_ported_localhost(client: TestClient) -> None:
    """vite 开发态：Origin localhost:3000 与代理后的 Host 不同，仍应放行。"""
    r = _post_book(client, "http://localhost:3000")
    assert r.status_code == 201, r.text


def test_csrf_still_rejects_foreign_origin(client: TestClient) -> None:
    """跨站 Origin（非 loopback、不等于请求 Host）仍必须 403。"""
    for origin in ("http://evil.example.com", "http://evil.example.com:8000"):
        r = _post_book(client, origin)
        assert r.status_code == 403, f"{origin}: {r.status_code}"
        assert "Origin 不匹配" in r.text


def test_csrf_rejects_foreign_origin_matching_host_prefix(client: TestClient) -> None:
    """Host 白名单不做前缀匹配：evil.example.com 伪装 10.x 前缀仍拒绝。"""
    r = _post_book(client, "http://10.180.105.86.evil.example.com")
    assert r.status_code == 403, r.text
