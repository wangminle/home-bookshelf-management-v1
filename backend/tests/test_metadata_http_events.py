"""BI-01/RES-006：metadata http 观察钩子测试。

get_json/get_text 吞异常返回 None 的行为保持不变；注册 listener 后，
成功/HTTP 错误（含 429 状态码）/网络异常/超时都会发出结构化事件，
供诊断工具区分「无结果」与「网络/限流故障」。
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

from app.services.metadata import http as metadata_http


def _setup_listener():
    events: list[dict] = []
    metadata_http.clear_response_listeners()
    metadata_http.add_response_listener(events.append)
    return events


def test_get_json_success_emits_event_with_status():
    events = _setup_listener()
    class FakeResp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with patch.object(metadata_http.urllib.request, "urlopen", return_value=FakeResp(json.dumps({"a": 1}).encode("utf-8"))):
        result = metadata_http.get_json("https://example.com/api")

    assert result == {"a": 1}
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "json" and event["status"] == 200
    assert event["error"] is None and event["elapsed_ms"] >= 0


def test_get_json_http_error_emits_status_code():
    events = _setup_listener()
    exc = urllib.error.HTTPError(
        "https://example.com/api", 429, "Too Many Requests", hdrs=None, fp=io.BytesIO(b"{}")
    )
    with patch.object(metadata_http.urllib.request, "urlopen", side_effect=exc):
        result = metadata_http.get_json("https://example.com/api")

    assert result is None  # 吞异常行为不变
    assert events[0]["status"] == 429
    assert "HTTPError" in events[0]["error"]


def test_get_json_network_error_emits_error_without_status():
    events = _setup_listener()
    with patch.object(
        metadata_http.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = metadata_http.get_json("https://example.com/api")

    assert result is None
    assert events[0]["status"] is None
    assert "URLError" in events[0]["error"]


def test_get_text_timeout_emits_timeout_event():
    events = _setup_listener()
    with patch.object(metadata_http.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
        result = metadata_http.get_text("https://example.com/page")

    assert result is None
    assert "TimeoutError" in events[0]["error"]
    assert events[0]["kind"] == "text"


def test_no_listener_keeps_behavior_unchanged():
    metadata_http.clear_response_listeners()
    with patch.object(
        metadata_http.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        assert metadata_http.get_json("https://example.com/api") is None


def test_broken_listener_never_breaks_request():
    events = _setup_listener()

    def bad_listener(_event):
        raise RuntimeError("钩子自身故障")

    metadata_http.add_response_listener(bad_listener)
    class FakeResp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with patch.object(metadata_http.urllib.request, "urlopen", return_value=FakeResp(json.dumps({"ok": True}).encode("utf-8"))):
        result = metadata_http.get_json("https://example.com/api")

    assert result == {"ok": True}
    assert len(events) == 1  # 好的 listener 仍收到事件
    metadata_http.clear_response_listeners()


# --- 安全回归：事件 URL 不得携带 API key 等敏感查询参数（P1，commit 5cf0507） ---

SECRET_URL = "https://www.googleapis.com/books/v1/volumes?q=isbn:9787020002207&maxResults=3&key=secret123"


def test_event_url_masks_api_key_on_success():
    events = _setup_listener()

    class FakeResp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with patch.object(metadata_http.urllib.request, "urlopen", return_value=FakeResp(b'{"items": []}')):
        result = metadata_http.get_json(SECRET_URL)

    assert result == {"items": []}
    event = events[0]
    assert "key=***" in event["url"]
    assert "secret123" not in event["url"]
    # 非敏感参数保留，便于诊断
    assert "q=isbn%3A9787020002207" in event["url"] or "q=isbn:9787020002207" in event["url"]
    assert "maxResults=3" in event["url"]
    # 事件其他字段也不得泄露原始密钥
    assert "secret123" not in str({k: v for k, v in event.items() if k != "elapsed_ms"})


def test_event_url_masks_api_key_on_http_error():
    events = _setup_listener()
    exc = urllib.error.HTTPError(SECRET_URL, 429, "Too Many Requests", hdrs=None, fp=io.BytesIO(b"{}"))
    with patch.object(metadata_http.urllib.request, "urlopen", side_effect=exc):
        result = metadata_http.get_json(SECRET_URL)

    assert result is None
    event = events[0]
    assert event["status"] == 429
    assert "key=***" in event["url"]
    assert "secret123" not in event["url"]
    assert "secret123" not in str({k: v for k, v in event.items() if k != "elapsed_ms"})


def test_event_url_masks_api_key_on_network_error():
    events = _setup_listener()
    with patch.object(
        metadata_http.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("connection refused for https://example.com?key=secret123"),
    ):
        result = metadata_http.get_json(SECRET_URL)

    assert result is None
    event = events[0]
    assert event["status"] is None
    assert "key=***" in event["url"]
    assert "secret123" not in event["url"]
    # 异常文本回显 URL 时也必须脱敏
    assert "secret123" not in event["error"]


def test_event_url_masks_api_key_on_timeout_for_get_text():
    events = _setup_listener()
    with patch.object(metadata_http.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
        result = metadata_http.get_text(SECRET_URL)

    assert result is None
    event = events[0]
    assert event["kind"] == "text"
    assert "key=***" in event["url"]
    assert "secret123" not in event["url"]


def test_sanitize_url_variants_and_no_query():
    assert metadata_http.sanitize_url(
        "https://api.example.com/x?api_key=abc&token=tok&access_token=at&secret=s&apikey=k&foo=bar"
    ) == "https://api.example.com/x?api_key=***&token=***&access_token=***&secret=***&apikey=***&foo=bar"
    # 无查询参数的 URL 原样返回
    assert metadata_http.sanitize_url("https://example.com/api") == "https://example.com/api"
    # 路径与锚点保留
    out = metadata_http.sanitize_url("https://example.com/v1/books?key=abc&page=2#frag")
    assert "page=2" in out and "#frag" in out and "key=***" in out


# BUG-288：密钥含 %2B 等编码字符时，异常回显的是 URL 原始写法，
# 只替换解码值会留下可还原的编码密钥。
ENCODED_SECRET_URL = "https://example.invalid/books?api_key=synthetic%2Bsecret%2F2026%3D&q=isbn"


def test_event_error_masks_percent_encoded_api_key():
    events = _setup_listener()
    with patch.object(
        metadata_http.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("failed request " + ENCODED_SECRET_URL),
    ):
        result = metadata_http.get_json(ENCODED_SECRET_URL)

    assert result is None
    event = events[0]
    assert "api_key=***" in event["url"]
    # 编码形态与解码形态都不得出现在事件任何字段中
    leaked = {k: v for k, v in event.items() if k != "elapsed_ms"}
    assert "synthetic%2Bsecret%2F2026%3D" not in str(leaked)
    assert "synthetic+secret/2026=" not in str(leaked)


def test_error_message_masks_encoded_variants():
    # 原始写法、quote/quote_plus 重编码、小写百分号形态全部脱敏
    exc = urllib.error.URLError(
        "failed request https://example.invalid/books?api_key=synthetic%2Bsecret%2F2026%3D&q=isbn"
    )
    msg = metadata_http._error_message(exc, ENCODED_SECRET_URL)
    assert "synthetic%2Bsecret%2F2026%3D" not in msg
    assert "synthetic+secret/2026=" not in msg
    assert "***" in msg
