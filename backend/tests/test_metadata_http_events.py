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
