"""BUG-261/262 回归：CLI 客户端"结果未知"分类与作者数组传递。

覆盖写入路径（intake/购买）的重复提交防护：
- 确定未提交（连接失败）→ RuntimeError，可安全重试；
- 可能已提交但回执丢失（读/写阶段 ReadError/RemoteProtocolError、2xx 响应体
  不可解析、读超时）→ ApiOutcomeUnknownError，必须待核对而非自动重发；
- add --authors 可重复传入，数组完整传给后端（多值表单字段）。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner
from unittest.mock import MagicMock, patch

from bookshelf.client import ApiOutcomeUnknownError, ApiTimeoutError, BookshelfClient
from bookshelf.main import app

runner = CliRunner()


class _FakeResponse:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("No JSON")
        return self._body


class _FakeHttpClient:
    """按 handler 返回响应或抛 httpx 异常的假 httpx.Client。"""

    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, url, headers=None, **kwargs):
        return self._handler(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self._handler("GET", url, **kwargs)


def _client_raising(exc: Exception) -> BookshelfClient:
    def handler(method, url, **kwargs):
        raise exc

    client = BookshelfClient(base_url="http://x")
    patcher = patch("bookshelf.client.httpx.Client",
                    lambda *a, **k: _FakeHttpClient(handler))
    return client, patcher


# --- BUG-261：确定未提交 vs 可能已提交 ----------------------------------------


def test_connect_error_is_definitely_not_submitted():
    client, patcher = _client_raising(httpx.ConnectError("refused"))
    with patcher:
        with pytest.raises(RuntimeError) as ei:
            client.show(1)
    assert "无法连接 API" in str(ei.value)  # 确定未提交，可安全重试


def test_connect_timeout_is_definitely_not_submitted():
    client, patcher = _client_raising(httpx.ConnectTimeout("dial"))
    with patcher:
        with pytest.raises(RuntimeError) as ei:
            client.show(1)
    assert "未建立连接" in str(ei.value)


@pytest.mark.parametrize("exc", [
    httpx.ReadError("eof"),
    httpx.RemoteProtocolError("bad http"),
    httpx.WriteError("broken"),
])
def test_read_phase_failures_are_outcome_unknown(exc):
    """连接建立后读写/协议失败：请求可能已提交，归类结果未知（BUG-261）。"""
    client, patcher = _client_raising(exc)
    with patcher:
        with pytest.raises(ApiOutcomeUnknownError) as ei:
            client.show(1)
    msg = str(ei.value)
    assert "回执丢失（可能已提交）" in msg
    assert exc.__class__.__name__ in msg


def test_read_timeout_is_outcome_unknown():
    client, patcher = _client_raising(httpx.ReadTimeout("slow"))
    with patcher:
        with pytest.raises(ApiTimeoutError) as ei:
            client.show(1)
    assert isinstance(ei.value, ApiOutcomeUnknownError)  # 超时是结果未知的一类


def test_2xx_unparseable_body_is_outcome_unknown():
    """2xx 但响应体不可解析：服务端很可能已处理写入，禁止自动重发。"""
    client = BookshelfClient(base_url="http://x")
    with patch("bookshelf.client.httpx.Client",
               lambda *a, **k: _FakeHttpClient(
                   lambda m, u, **kw: _FakeResponse(200, body=None, text="<html>"))):
        with pytest.raises(ApiOutcomeUnknownError) as ei:
            client.show(1)
    assert "HTTP 200 响应体不可解析" in str(ei.value)


def test_read_5xx_unparseable_body_is_read_failure():
    """GET 不产生附加写入，错误仍走普通读取失败；不能推断写请求未提交。"""
    client = BookshelfClient(base_url="http://x")
    with patch("bookshelf.client.httpx.Client",
               lambda *a, **k: _FakeHttpClient(
                   lambda m, u, **kw: _FakeResponse(502, body=None, text="<html>"))):
        with pytest.raises(RuntimeError) as ei:
            client.show(1)
    assert "API 返回非 JSON（502）" in str(ei.value)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize("body", [None, {"detail": "server failure"}])
def test_write_5xx_is_outcome_unknown_regardless_of_body(status, body):
    client = BookshelfClient(base_url="http://x")
    with patch("bookshelf.client.httpx.Client",
               lambda *a, **k: _FakeHttpClient(
                   lambda m, u, **kw: _FakeResponse(status, body=body, text="upstream error"))):
        with pytest.raises(ApiOutcomeUnknownError, match=f"HTTP {status}"):
            client.purchase(book_id=1, price=9.9)


def test_write_400_is_definite_business_rejection():
    client = BookshelfClient(base_url="http://x")
    with patch("bookshelf.client.httpx.Client",
               lambda *a, **k: _FakeHttpClient(
                   lambda m, u, **kw: _FakeResponse(400, body={"detail": "价格必须大于0"}))):
        with pytest.raises(RuntimeError) as exc:
            client.purchase(book_id=1, price=-1)
    assert not isinstance(exc.value, ApiOutcomeUnknownError)
    assert "[HTTP 400]" in str(exc.value)


def test_purchase_read_error_is_outcome_unknown():
    """购买（写入）场景：ReadError → 结果未知，防重复扣款/重复购买记录。"""
    client, patcher = _client_raising(httpx.ReadError("eof"))
    with patcher:
        with pytest.raises(ApiOutcomeUnknownError):
            client.purchase(book_id=1, price=9.9)


def test_note_remote_protocol_error_is_outcome_unknown():
    """笔记（写入）场景：RemoteProtocolError → 结果未知。"""
    client, patcher = _client_raising(httpx.RemoteProtocolError("bad"))
    with patcher:
        with pytest.raises(ApiOutcomeUnknownError):
            client.note(book_id=1, content="x")


# --- BUG-262：作者数组从 CLI 传到后端 -----------------------------------------


def test_add_passes_authors_array_as_multivalue_form():
    """带图 multipart：authors 以多值表单字段重复传递，不丢作者。"""
    seen = {}

    def handler(method, url, headers=None, data=None, files=None, **kwargs):
        seen["data"] = data
        return _FakeResponse(200, {"ok": True, "data": {"book": {"id": 1},
                                                        "already_exists": False}})

    client = BookshelfClient(base_url="http://x")
    with patch("bookshelf.client.httpx.Client",
               lambda *a, **k: _FakeHttpClient(handler)):
        client.add(title="雾中灯塔", authors=["陈予安", "林晚"], image=Path(__file__))
    assert seen["data"]["authors"] == ["陈予安", "林晚"]


def test_add_json_passes_authors_array():
    seen = {}

    def handler(method, url, headers=None, json=None, **kwargs):
        seen["json"] = json
        return _FakeResponse(200, {"ok": True, "data": {"book": {"id": 1},
                                                        "already_exists": False}})

    client = BookshelfClient(base_url="http://x")
    with patch("bookshelf.client.httpx.Client",
               lambda *a, **k: _FakeHttpClient(handler)):
        client.add(title="雾中灯塔", authors=["陈予安", "林晚"])
    assert seen["json"]["authors"] == ["陈予安", "林晚"]


def test_cli_add_accepts_repeated_authors_option():
    """typer add --authors 可重复传入，完整转发给 client.add。"""
    mock_client = MagicMock()
    mock_client.add.return_value = {"ok": True, "data": {"message": "ok"}}
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(
            app, ["add", "--title", "雾中灯塔", "--authors", "陈予安", "--authors", "林晚"])
    assert result.exit_code == 0
    assert mock_client.add.call_args.kwargs["authors"] == ["陈予安", "林晚"]
