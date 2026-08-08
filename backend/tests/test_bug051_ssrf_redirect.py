"""BUG-051: download_cover 不得跟随重定向到内网/回环地址。"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.config import settings
from app.services import storage as storage_mod
from app.services.storage import download_cover

_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # minimal JPEG-ish bytes


def test_download_cover_rechecks_each_redirect(tmp_path: Path, monkeypatch):
    """初始 URL 放行后，若 302 指向回环且可下载，未校验跳转时会成功落盘；修复后须拒绝。"""
    data_dir = tmp_path / "data"
    covers = data_dir / "covers"
    covers.mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)

    state: dict[str, int] = {"port": 0}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/cover"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{state['port']}/evil.jpg")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(_FAKE_JPEG)))
            self.end_headers()
            self.wfile.write(_FAKE_JPEG)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    state["port"] = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    initial = f"http://127.0.0.1:{state['port']}/cover.jpg"
    evil = f"http://127.0.0.1:{state['port']}/evil.jpg"
    checked_urls: list[str] = []

    real_is_safe = storage_mod._is_safe_url

    def _selective(url: str) -> bool:
        checked_urls.append(url)
        if url == initial:
            return True
        return real_is_safe(url)

    monkeypatch.setattr(storage_mod, "_is_safe_url", _selective)
    try:
        result = download_cover(initial, "ssrf-redirect")
        assert result is None, "跟随到回环地址的重定向必须被拒绝"
        assert any(u == evil or u.rstrip("/") == evil.rstrip("/") for u in checked_urls), (
            f"应校验跳转目标，实际校验了: {checked_urls}"
        )
        leftovers = [p for p in covers.iterdir() if p.is_file()]
        assert leftovers == [], f"不得留下半下载文件: {leftovers}"
    finally:
        server.shutdown()
