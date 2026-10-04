"""BI-07 回归：统一视觉服务（契约 §4.6）。

覆盖：禁用/配置缺失、超时、429 重试与上限、鉴权不重试、非 JSON、字段类型
错误、ISBN 校验失败、无依据字段留空、缓存命中（不重复付费调用）、密钥不
落缓存/日志。全部注入假 llm_settings 行与假 HTTP 层，不触网。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models import LlmSettings
from app.services import vision
from app.services.vision import (
    ERR_DISABLED,
    ERR_INVALID_RESPONSE,
    ERR_NOT_CONFIGURED,
    ERR_RATE_LIMITED,
    ERR_TIMEOUT,
    ERR_UNAUTHORIZED,
    PROMPT_VERSION,
    TASK_TYPE,
    VisionServiceResult,
    model_fingerprint,
    parse_model_content,
    recognize_cover_fields,
)


def _row(**kw) -> LlmSettings:
    base = dict(
        enabled=True, display_name="test", base_url="https://api.example.com/v1",
        model_id="gpt-test", api_key="sk-secret-key", timeout_seconds=5,
        max_tokens=256, temperature=0.0, image_detail="auto",
    )
    base.update(kw)
    return LlmSettings(**base)


_UNSET = object()


def _ok_payload(content_json: dict, usage=_UNSET) -> tuple[int, dict, str]:
    if usage is _UNSET:
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    text = json.dumps({"choices": [{"message": {"content": json.dumps(content_json, ensure_ascii=False)}}],
                       "usage": usage})
    return 200, json.loads(text), text


def _img(tmp_path: Path, content: bytes = b"\x89PNG-fake") -> Path:
    p = tmp_path / "cover.png"
    p.write_bytes(content)
    return p


def _run(tmp_path, row, http=None, *, use_cache=True, cache_dir=None):
    calls: list[dict] = []

    def fake_post(url, payload, headers, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        if callable(http):
            return http(calls)
        return http  # 固定返回

    with patch("app.services.vision.load_row", return_value=row), \
            patch("app.services.vision._post_chat_completion", side_effect=fake_post), \
            patch("app.services.vision._RETRY_BACKOFF_SEC", (0, 0)):
        result = recognize_cover_fields(
            None, _img(tmp_path), use_cache=use_cache,
            cache_dir=cache_dir if cache_dir is not None else (tmp_path / "cache"))
    return result, calls


# ── 配置态 ──

def test_disabled_returns_without_http_call(tmp_path):
    result, calls = _run(tmp_path, _row(enabled=False))
    assert result.ok is False and result.error_code == ERR_DISABLED
    assert calls == []


def test_missing_config_detected(tmp_path):
    result, calls = _run(tmp_path, _row(api_key=None))
    assert result.ok is False and result.error_code == ERR_NOT_CONFIGURED
    assert calls == []


def test_unreadable_image(tmp_path):
    with patch("app.services.vision.load_row", return_value=_row()):
        result = recognize_cover_fields(None, tmp_path / "missing.png")
    assert result.ok is False and result.error_code == "image_unreadable"


# ── 成功路径与字段处理 ──

def test_ok_full_fields_with_usage(tmp_path):
    content = {
        "title": "翦商", "subtitle": "殷周之变与华夏新生", "authors": ["李硕"],
        "isbn": "9787553820217",
        "readable": {"title": True, "subtitle": True, "authors": True, "isbn": False},
        "confidence": 0.92,
    }
    result, calls = _run(tmp_path, _row(), http=_ok_payload(content))
    assert result.ok and result.cache_hit is False
    assert result.candidate.title == "翦商"
    assert result.candidate.subtitle == "殷周之变与华夏新生"
    assert result.candidate.authors == ["李硕"]
    assert result.candidate.isbn == "9787553820217"
    assert result.candidate.fields["title"].readable is True
    assert result.candidate.confidence == 0.92
    assert result.usage == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    assert result.model_fingerprint and result.image_sha256
    # 请求结构：OpenAI 兼容 chat/completions，密钥只在请求头，不进 payload
    assert calls[0]["url"].endswith("/chat/completions")
    assert calls[0]["headers"]["Authorization"].startswith("Bearer ")
    assert "sk-secret-key" not in json.dumps(calls[0]["payload"])


def test_no_evidence_fields_left_empty(tmp_path):
    """无依据字段留空：不可辨的字段是 null，模型不得编造。"""
    content = {"title": "小学问", "subtitle": None, "authors": [], "isbn": None,
               "readable": {"title": True, "isbn": False}, "confidence": 0.5}
    result, _ = _run(tmp_path, _row(), http=_ok_payload(content, usage=None))
    assert result.ok
    assert result.candidate.isbn is None and result.candidate.authors == []
    assert result.usage is None  # 无 usage 如实为空


def test_invalid_isbn_checksum_dropped_with_warning(tmp_path):
    content = {"title": "活着", "authors": ["余华"], "isbn": "9787506365430"}  # 校验位错
    result, _ = _run(tmp_path, _row(), http=_ok_payload(content))
    assert result.ok
    assert result.candidate.isbn is None
    codes = [w["code"] for w in result.candidate.warnings]
    assert "isbn_invalid_checksum" in codes


def test_field_type_errors_degrade_not_crash(tmp_path):
    content = {"title": 42, "subtitle": None, "authors": "刘慈欣", "isbn": True,
               "confidence": "high"}
    result, _ = _run(tmp_path, _row(), http=_ok_payload(content))
    assert result.ok
    c = result.candidate
    assert c.title == "42"           # 数字强转
    assert c.authors == ["刘慈欣"]    # 字符串按单作者 + 警告
    assert c.isbn is None            # 非文本 ISBN 丢弃
    assert c.confidence is None      # 非数字置信度忽略
    codes = [w["code"] for w in c.warnings]
    assert "field_type_error" in codes


def test_json_with_code_fence_and_prose_parsed(tmp_path):
    text = '{"choices": [{"message": {"content": "```json\\n{\\"title\\": \\"三体\\", \\"authors\\": [\\"刘慈欣\\"]}\\n```"}}]}'
    result, _ = _run(tmp_path, _row(), http=(200, json.loads(text), text))
    assert result.ok and result.candidate.title == "三体"


def test_non_json_response_is_invalid_response_no_retry(tmp_path):
    result, calls = _run(tmp_path, _row(), http=(200, None, "<html>gateway</html>"))
    assert result.ok is False and result.error_code == ERR_INVALID_RESPONSE
    assert len(calls) == 1  # 响应完整到达，重试无意义


def test_content_missing_is_invalid_response(tmp_path):
    payload = {"choices": [{"message": {}}]}
    result, _ = _run(tmp_path, _row(), http=(200, payload, json.dumps(payload)))
    assert result.ok is False and result.error_code == ERR_INVALID_RESPONSE


# ── 重试与限流 ──

def test_rate_limited_then_success_retries(tmp_path):
    content = {"title": "三体", "authors": ["刘慈欣"]}
    states = {"n": 0}

    def http(calls):
        states["n"] += 1
        if states["n"] == 1:
            return (429, {"error": "rate"}, "rate limited")
        return _ok_payload(content)

    result, calls = _run(tmp_path, _row(), http=http)
    assert result.ok and result.attempts == 2 and len(calls) == 2


def test_rate_limited_exhausts_attempts(tmp_path):
    result, calls = _run(tmp_path, _row(), http=(429, {}, "rate"))
    assert result.ok is False and result.error_code == ERR_RATE_LIMITED
    assert len(calls) == 3  # 上限 3 次后放弃


def test_unauthorized_never_retries(tmp_path):
    result, calls = _run(tmp_path, _row(), http=(401, {"error": "bad key"}, "unauthorized"))
    assert result.ok is False and result.error_code == ERR_UNAUTHORIZED
    assert len(calls) == 1


def test_timeout_retries_then_reports_unknown_billing_risk(tmp_path):
    from app.services.vision import _HttpTimeout

    def http(calls):
        raise _HttpTimeout("read timeout")

    result, calls = _run(tmp_path, _row(), http=http)
    assert result.ok is False and result.error_code == ERR_TIMEOUT
    assert len(calls) == 3
    assert "潜在重复计费" in result.message  # 结果未知如实注明


def test_client_error_no_retry(tmp_path):
    result, calls = _run(tmp_path, _row(), http=(400, {"error": "bad request"}, "bad"))
    assert result.ok is False and result.error_code == "client_error"
    assert len(calls) == 1


# ── 缓存 ──

def test_cache_hit_skips_paid_call(tmp_path):
    content = {"title": "三体", "authors": ["刘慈欣"]}
    cache = tmp_path / "cache"
    result1, calls1 = _run(tmp_path, _row(), http=_ok_payload(content), cache_dir=cache)
    assert result1.cache_hit is False and len(calls1) == 1

    result2, calls2 = _run(tmp_path, _row(), http=_ok_payload(content), cache_dir=cache)
    assert result2.cache_hit is True and calls2 == []
    assert result2.candidate.title == "三体"
    # usage/耗时来自缓存记录
    assert result2.usage == result1.usage


def test_cache_key_binds_image_content_fingerprint_and_prompt(tmp_path):
    content = {"title": "三体", "authors": ["刘慈欣"]}
    cache = tmp_path / "cache"
    _run(tmp_path, _row(), http=_ok_payload(content), cache_dir=cache)
    # 图片内容变化 → 不命中
    result2, _ = _run(tmp_path, _row(), http=_ok_payload(content),
                      cache_dir=cache)
    img2 = tmp_path / "cover.png"
    img2.write_bytes(b"\x89PNG-different")  # _run 每次重写同路径图片，这里直接改内容再跑
    with patch("app.services.vision.load_row", return_value=_row()), \
            patch("app.services.vision._post_chat_completion", return_value=_ok_payload(content)), \
            patch("app.services.vision._RETRY_BACKOFF_SEC", (0, 0)):
        result3 = recognize_cover_fields(None, img2, use_cache=True, cache_dir=cache)
    assert result3.cache_hit is False
    # 模型配置变化 → 不命中
    result4, _ = _run(tmp_path, _row(model_id="gpt-other"), http=_ok_payload(content), cache_dir=cache)
    assert result4.cache_hit is False


def test_cache_file_never_contains_api_key(tmp_path):
    content = {"title": "三体"}
    cache = tmp_path / "cache"
    _run(tmp_path, _row(api_key="sk-super-secret"), http=_ok_payload(content), cache_dir=cache)
    for f in cache.glob("*.json"):
        raw = f.read_text(encoding="utf-8")
        assert "sk-super-secret" not in raw


def test_no_cache_when_disabled(tmp_path):
    content = {"title": "三体"}
    cache = tmp_path / "cache"
    _run(tmp_path, _row(), http=_ok_payload(content), use_cache=False, cache_dir=cache)
    assert not cache.exists() or not list(cache.glob("*.json"))


# ── 指纹与身份 ──

def test_model_fingerprint_stable_and_key_safe():
    row1, row2 = _row(), _row()
    assert model_fingerprint(row1) == model_fingerprint(row2)
    assert model_fingerprint(row1) != model_fingerprint(_row(model_id="other"))
    assert model_fingerprint(row1) != model_fingerprint(_row(api_key="sk-another"))
    assert len(model_fingerprint(row1)) == 16
    assert "sk-secret-key" not in model_fingerprint(row1)


def test_result_roundtrip_via_dict():
    result = VisionServiceResult(ok=True, message="x", model_fingerprint="abc",
                                 usage={"total_tokens": 5})
    cloned = VisionServiceResult.from_dict(result.to_dict())
    assert cloned.ok and cloned.model_fingerprint == "abc" and cloned.usage["total_tokens"] == 5


def test_identity_constants_frozen():
    assert PROMPT_VERSION == "vision-cover-v1"
    assert TASK_TYPE == "cover_title_author"


# ── parse_model_content 单元 ──

def test_parse_model_content_rejects_garbage():
    with pytest.raises(ValueError):
        parse_model_content("完全不是 JSON")


def test_parse_model_content_authors_cap():
    content = {"title": "x", "authors": [f"作{i}" for i in range(30)]}
    c = parse_model_content(json.dumps(content))
    assert len(c.authors) == 20  # _MAX_AUTHORS 截断
