"""BUG-131/132/133 回归测试。

- BUG-131: HTTPS 钉 IP 后 SNI/证书校验仍用原始主机名；IPv6 netloc 加方括号；
  storage.py 重复定义已清除（模块内同名对象唯一）。
- BUG-132: 配置共享密钥后，携带渠道头的请求必须带有效 HMAC 签名。
- BUG-133: 无 ISBN 同书名并发入库只建一本书（临界区覆盖到 commit + 跨进程文件锁）。
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import threading
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Book
from app.services import storage
from app.services.intake import IntakeInput, intake_book
from app.utils.book_helpers import normalize_title


# ---------- BUG-131 ----------


def test_pin_url_ipv4_with_port():
    assert (
        storage._pin_url("https://example.com:8443/a.jpg", "93.184.216.34")
        == "https://93.184.216.34:8443/a.jpg"
    )


def test_pin_url_ipv6_brackets():
    assert (
        storage._pin_url("https://example.com/a.jpg", "2606:2800:220:1:248:1893:25c8:1946")
        == "https://[2606:2800:220:1:248:1893:25c8:1946]/a.jpg"
    )


def test_format_netloc_ipv6_host_header():
    assert (
        storage._format_netloc("2606:2800:220:1:248:1893:25c8:1946", 8443)
        == "[2606:2800:220:1:248:1893:25c8:1946]:8443"
    )
    assert storage._format_netloc("example.com", None) == "example.com"


def test_pinned_request_stashes_original_hostname():
    req = storage._build_pinned_request("https://covers.example.com:8443/x.jpg", "93.184.216.34")
    assert req.full_url == "https://93.184.216.34:8443/x.jpg"
    assert req._bookshelf_server_hostname == "covers.example.com"
    assert req.headers["Host"] == "covers.example.com:8443"


def test_safe_opener_uses_pinned_https_handler():
    assert any(isinstance(h, storage._PinnedHTTPSHandler) for h in storage._SAFE_OPENER.handlers)


def test_pinned_https_connect_uses_original_hostname_for_tls(monkeypatch):
    """TCP 打 IP，但 wrap_socket 的 server_hostname 必须是原始域名（SNI + 证书校验）。"""
    conn = storage._PinnedHTTPSConnection("93.184.216.34", server_hostname="covers.example.com")
    fake_sock = object()
    wrapped = object()
    monkeypatch.setattr(
        http.client.HTTPConnection, "connect", lambda self: setattr(self, "sock", fake_sock)
    )

    class _Ctx:
        def wrap_socket(self, sock, *, server_hostname):
            assert sock is fake_sock
            assert server_hostname == "covers.example.com"
            return wrapped

    conn._context = _Ctx()
    conn.connect()
    assert conn.sock is wrapped


def test_pinned_https_connect_prefers_pinned_hostname_over_tunnel(monkeypatch):
    """经代理 CONNECT 隧道时 _tunnel_host 是钉住的 IP，SNI 仍须用原始域名。"""
    conn = storage._PinnedHTTPSConnection("93.184.216.34", server_hostname="covers.example.com")
    conn._tunnel_host = "93.184.216.34"  # 模拟代理隧道
    fake_sock = object()
    wrapped = object()
    monkeypatch.setattr(
        http.client.HTTPConnection, "connect", lambda self: setattr(self, "sock", fake_sock)
    )

    class _Ctx:
        def wrap_socket(self, sock, *, server_hostname):
            assert server_hostname == "covers.example.com"
            return wrapped

    conn._context = _Ctx()
    conn.connect()
    assert conn.sock is wrapped


# ---------- BUG-132 ----------


def _sign(secret: str, channel: str, external: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), f"{channel}:{external}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _seed_owner_and_book(client) -> tuple[int, int]:
    m = client.post("/api/v1/members", json={"name": "甲", "role": "owner"})
    assert m.status_code == 201
    member_id = m.json()["data"]["id"]
    b = client.post("/api/v1/books", json={"title": "签名校验书"})
    assert b.status_code == 201
    return member_id, b.json()["data"]["id"]


def test_channel_signature_required_when_secret_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "channel_signing_secret", "s3cret")
    member_id, book_id = _seed_owner_and_book(client)
    # 引导期（尚无绑定）匿名 bind 仍允许，建立白名单
    assert (
        client.post(
            "/api/v1/members/bind",
            json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_owner"},
        ).status_code
        == 200
    )

    forged = {"X-Channel": "feishu", "X-External-User-Id": "ou_owner"}
    # 伪造已知 owner 外部 ID、无签名 → 403
    r = client.post(
        f"/api/v1/books/{book_id}/progress", json={"status": "reading"}, headers=forged
    )
    assert r.status_code == 403, r.text
    # 错误签名 → 403
    r = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"status": "reading"},
        headers={**forged, "X-Channel-Signature": "0" * 64},
    )
    assert r.status_code == 403, r.text
    # 正确签名 → 通过
    r = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"status": "reading"},
        headers={**forged, "X-Channel-Signature": _sign("s3cret", "feishu", "ou_owner")},
    )
    assert r.status_code == 201, r.text


def test_channel_signature_not_required_when_secret_unset(client, monkeypatch):
    """未配置密钥时维持可信局域网边界：绑定渠道头无需签名即可写。"""
    monkeypatch.setattr(settings, "channel_signing_secret", None)
    member_id, book_id = _seed_owner_and_book(client)
    assert (
        client.post(
            "/api/v1/members/bind",
            json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_owner"},
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"status": "reading"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_owner"},
    )
    assert r.status_code == 201, r.text


# ---------- BUG-133 ----------


def test_concurrent_intake_same_title_creates_single_book(db_engine):
    """多线程并发无 ISBN 同书名入库：临界区覆盖 commit，最终只建一本书。"""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    errors: list[Exception] = []

    def worker():
        try:
            with SessionLocal() as s:
                intake_book(s, IntakeInput(title="并发测试书", author="并发作者"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with patch("app.services.intake.fetch_metadata", return_value=None):
        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not errors
    with SessionLocal() as s:
        count = s.scalar(
            select(func.count())
            .select_from(Book)
            .where(Book.normalized_title == normalize_title("并发测试书"))
        )
    assert count == 1
