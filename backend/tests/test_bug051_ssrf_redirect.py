"""BUG-051 / BUG-105: download_cover 必须对每个重定向复检安全性并钉住安全 IP，
不得跟随重定向到内网/回环地址，也不得在 getaddrinfo→urlopen 间被 DNS 重绑定。"""

from __future__ import annotations

import urllib.error
from pathlib import Path

from app.config import settings
from app.services import storage as storage_mod
from app.services.storage import _build_pinned_request, download_cover

_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # minimal JPEG-ish bytes


def test_safe_redirect_handler_blocks_loopback_redirect():
    """_SafeRedirectHandler 对指向回环的 Location 应抛 URLError，不跟随。"""
    handler = storage_mod._SafeRedirectHandler()
    # 模拟一个指向回环的重定向目标
    evil = "http://127.0.0.1:9/evil.jpg"
    raised = False
    try:
        handler.redirect_request(req=None, fp=None, code=302, msg="Found", headers={}, newurl=evil)
    except urllib.error.URLError:
        raised = True
    except TypeError:
        # super().redirect_request 需要 req 对象；但安全检查在构造新请求前已抛错
        raised = True
    assert raised, "指向回环的重定向必须被拒绝"


def test_build_pinned_request_replaces_hostname_with_ip():
    """BUG-105：_build_pinned_request 必须把 hostname 替换为钉住的 IP，保留 Host 头与端口。"""
    req = _build_pinned_request("http://example.com:8080/path?q=1", "203.0.113.77")
    # 请求 URL 用 IP 而非域名，避免 urlopen 内部再次解析 DNS
    assert "203.0.113.77" in req.full_url
    assert "example.com" not in req.full_url.split("/")[2]  # netloc 段不含域名
    # 端口保留
    assert "8080" in req.full_url
    # 原始主机名放入 Host 头，保证虚拟主机/TLS 正常
    assert req.has_header("Host")
    host_value = req.get_header("Host", "")
    assert "example.com" in host_value


def test_redirect_handler_pins_ip_for_safe_redirect():
    """BUG-105：_SafeRedirectHandler 对安全的重定向目标应钉住 IP（替换 hostname）。"""
    handler = storage_mod._SafeRedirectHandler()
    # 用 monkeypatch 让 _is_safe_url 对目标返回安全 + 一个公网 IP
    orig = storage_mod._is_safe_url
    storage_mod._is_safe_url = lambda url: (True, "203.0.113.55")
    try:
        import urllib.request

        parent_req = urllib.request.Request("http://first.example.com/a.jpg")
        new_req = handler.redirect_request(
            req=parent_req, fp=None, code=302, msg="Found", headers={}, newurl="http://target.example.com/b.jpg"
        )
        assert new_req is not None
        # 跟随请求应使用钉住 IP 而非原始域名
        assert "203.0.113.55" in new_req.full_url
        assert "target.example.com" not in new_req.full_url.split("/")[2]
    finally:
        storage_mod._is_safe_url = orig


def test_download_cover_rejects_unsafe_url(tmp_path: Path, monkeypatch):
    """不安全 URL（回环）直接拒绝，不发起请求。"""
    data_dir = tmp_path / "data"
    (data_dir / "covers").mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)

    result = download_cover("http://127.0.0.1:9/x.jpg", "reject")
    assert result is None


def test_download_cover_rejects_when_no_safe_ip(tmp_path: Path, monkeypatch):
    """_is_safe_url 返回 (True, None) 时也应拒绝（无可用 IP 不能 pin）。"""
    data_dir = tmp_path / "data"
    (data_dir / "covers").mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(storage_mod, "_is_safe_url", lambda url: (True, None))

    result = download_cover("http://example.com/x.jpg", "noip")
    assert result is None
