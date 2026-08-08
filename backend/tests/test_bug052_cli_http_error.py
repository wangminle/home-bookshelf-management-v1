"""BUG-052: CLI _request 遇 httpx.HTTPError 应转成 RuntimeError，不抛裸 traceback。"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

# 保证可 import cli 包（与 backend 并列）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "cli") not in sys.path:
    sys.path.insert(0, str(_ROOT / "cli"))

from bookshelf.client import BookshelfClient  # noqa: E402


def test_request_connect_error_becomes_runtime_error(monkeypatch):
    client = BookshelfClient(base_url="http://127.0.0.1:9")

    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, *args, **kwargs):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _Boom())

    with pytest.raises(RuntimeError, match="无法连接|网络|连接") as exc_info:
        client.find(keyword="x")
    assert not isinstance(exc_info.value.__cause__, httpx.HTTPError) or "ConnectError" not in type(
        exc_info.value
    ).__name__
