"""统一视觉识书服务（BI-07，规划 §4.6）。

读取 Owner 配置的 llm_settings，对一张封面照片生成**结构化候选书目**
（title/subtitle/authors/isbn，逐字段可读性与置信度）。模型只生成候选，
本服务不写图书库、不调用任何图书写入路径。

稳定性约束（契约 §4.6）：
- 请求处理覆盖：禁用/配置缺失、超时、429、非 JSON、字段类型错误、ISBN 校验失败；
- 鉴权错误不重试；限流/临时错误有次数上限与退避；
- 结果未知（超时耗尽重试）时如实说明潜在重复计费，不承诺外部 API 恰好一次；
- 按图片哈希 + 模型配置指纹 + 提示词版本缓存，重跑命中缓存不再付费调用；
- 日志与缓存均不含 API Key 与请求头；配置指纹只用 Key 的哈希前缀参与。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.services.llm_settings import load_row
from app.utils.book_helpers import is_valid_isbn, normalize_isbn

logger = logging.getLogger(__name__)

# 门控身份三元组（BI-10）：评测结果与 --yes 门控据此绑定，换任一项旧证据不通用
PROMPT_VERSION = "vision-cover-v1"
TASK_TYPE = "cover_title_author"

# 预算与限额：请求超时用 llm_settings.timeout_seconds；这里限制其余维度
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_RESPONSE_CHARS = 64_000
_MAX_AUTHORS = 20
_MAX_ATTEMPTS = 3  # 1 次初始 + 至多 2 次重试（限流/临时错误）
_RETRY_BACKOFF_SEC = (1.0, 2.0)

# 稳定错误码（评测报告与门控共用）
ERR_DISABLED = "disabled"
ERR_NOT_CONFIGURED = "not_configured"
ERR_IMAGE_UNREADABLE = "image_unreadable"
ERR_IMAGE_TOO_LARGE = "image_too_large"
ERR_TIMEOUT = "timeout"
ERR_RATE_LIMITED = "rate_limited"
ERR_UNAUTHORIZED = "unauthorized"
ERR_SERVER_ERROR = "server_error"
ERR_CLIENT_ERROR = "client_error"
ERR_NETWORK = "network_error"
ERR_INVALID_RESPONSE = "invalid_response"

# 鉴权错误（401/403）与其他 4xx 不重试；仅这些可重试
_RETRYABLE_ERRORS = {ERR_TIMEOUT, ERR_RATE_LIMITED, ERR_SERVER_ERROR, ERR_NETWORK}

_SYSTEM_PROMPT = (
    "你是图书封面识别助手。只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。"
    "字段：title（书名，封面不可辨则 null）、subtitle（副题，没有则 null）、"
    "authors（作者数组，按封面署名顺序；封面未署作者则为空数组，不要猜测）、"
    "isbn（仅当封面上可见且可完整辨认 ISBN/条码数字时填写，否则 null，绝不由书名推测）、"
    "readable（对象，各字段是否可读：title/subtitle/authors/isbn 为 true/false）、"
    "confidence（0 到 1 的小数，总体置信度）。"
    "看不清的字段必须留 null/空，不得编造。"
)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


class _HttpTimeout(Exception):
    pass


class _HttpNetwork(Exception):
    pass


@dataclass
class VisionField:
    """逐字段结果：value 为识别值（不可辨为 None）；readable 为模型自报可读性。"""

    value: str | None = None
    readable: bool | None = None
    confidence: float | None = None


@dataclass
class VisionCandidate:
    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = field(default_factory=list)
    isbn: str | None = None
    fields: dict[str, VisionField] = field(default_factory=dict)
    confidence: float | None = None
    warnings: list[dict] = field(default_factory=list)


@dataclass
class VisionServiceResult:
    ok: bool
    candidate: VisionCandidate | None = None
    error_code: str | None = None
    message: str = ""
    elapsed_ms: float = 0.0
    cache_hit: bool = False
    attempts: int = 0
    usage: dict | None = None  # {"prompt_tokens", "completion_tokens", "total_tokens"}，提供方可得时
    model_fingerprint: str | None = None
    prompt_version: str = PROMPT_VERSION
    task_type: str = TASK_TYPE
    image_sha256: str | None = None

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VisionServiceResult":
        candidate = None
        if isinstance(data.get("candidate"), dict):
            c = data["candidate"]
            fields = {
                name: VisionField(**f) if isinstance(f, dict) else VisionField()
                for name, f in (c.get("fields") or {}).items()
            }
            candidate = VisionCandidate(
                title=c.get("title"), subtitle=c.get("subtitle"),
                authors=list(c.get("authors") or []),
                isbn=c.get("isbn"), fields=fields,
                confidence=c.get("confidence"),
                warnings=list(c.get("warnings") or []),
            )
        return cls(
            ok=bool(data.get("ok")), candidate=candidate,
            error_code=data.get("error_code"), message=data.get("message") or "",
            elapsed_ms=data.get("elapsed_ms") or 0.0,
            cache_hit=bool(data.get("cache_hit")),
            attempts=data.get("attempts") or 0,
            usage=data.get("usage"),
            model_fingerprint=data.get("model_fingerprint"),
            prompt_version=data.get("prompt_version") or PROMPT_VERSION,
            task_type=data.get("task_type") or TASK_TYPE,
            image_sha256=data.get("image_sha256"),
        )


def model_fingerprint(row) -> str:
    """模型配置指纹：参与缓存键与门控身份；API Key 只以哈希前缀参与，原文绝不落缓存/日志。"""
    key_part = hashlib.sha256((row.api_key or "").encode("utf-8")).hexdigest()[:12]
    raw = "|".join(str(x) for x in (
        row.base_url, row.model_id, key_part,
        row.timeout_seconds, row.max_tokens, row.temperature, row.image_detail,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cache_key(image_sha: str, fingerprint: str) -> str:
    return hashlib.sha256(
        f"{image_sha}|{fingerprint}|{PROMPT_VERSION}|{TASK_TYPE}".encode("utf-8")
    ).hexdigest()


def _cache_dir(cache_dir: Path | None) -> Path:
    return cache_dir or (settings.data_dir / "vision_cache")


def _load_cache(cache_dir: Path, key: str) -> VisionServiceResult | None:
    path = cache_dir / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("key") != key:
        return None
    result = VisionServiceResult.from_dict(data.get("result") or {})
    result.cache_hit = True
    return result


def _save_cache(cache_dir: Path, key: str, result: VisionServiceResult) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{key}.json"
        path.write_text(json.dumps(
            {"schema": 1, "key": key, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "result": result.to_dict()}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("视觉识别缓存写入失败（忽略，不影响结果）", exc_info=False)


def _fail(error_code: str, message: str, **kw) -> VisionServiceResult:
    return VisionServiceResult(ok=False, error_code=error_code, message=message, **kw)


def _post_chat_completion(url: str, payload: dict, headers: dict, timeout: float):
    """单次 HTTP 调用（测试注入点）。返回 (status, parsed_json|None, text)。

    抛 _HttpTimeout / _HttpNetwork 由上层分类重试。
    """
    import httpx

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise _HttpTimeout(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise _HttpNetwork(exc.__class__.__name__) from exc
    text = resp.text[:_MAX_RESPONSE_CHARS]
    try:
        return resp.status_code, resp.json(), text
    except ValueError:
        return resp.status_code, None, text


def _clamp_confidence(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(min(max(float(value), 0.0), 1.0), 4)


def _clean_text_field(value) -> tuple[str | None, bool]:
    """文本字段清洗：None/空串→None；数字强转 str；其他类型视为字段类型错误。"""
    if value is None:
        return None, False
    if isinstance(value, str):
        v = value.strip()
        return (v or None), False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value), False
    return None, True


def parse_model_content(content) -> VisionCandidate:
    """解析模型输出为候选；类型异常降级为警告，不崩溃、不编造。"""
    warnings: list[dict] = []
    text = (content or "").strip()
    fenced = _JSON_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    data = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                data = None
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")

    candidate = VisionCandidate()
    readable = data.get("readable") if isinstance(data.get("readable"), dict) else {}

    for name in ("title", "subtitle", "isbn"):
        raw = data.get(name)
        value, type_error = _clean_text_field(raw)
        if type_error:
            warnings.append({"code": "field_type_error", "field": name,
                             "message": f"{name} 字段类型异常已忽略：{raw!r}"})
        if name == "isbn" and value:
            normalized = normalize_isbn(value)
            if not normalized or not is_valid_isbn(normalized):
                warnings.append({"code": "isbn_invalid_checksum", "field": "isbn",
                                 "message": f"模型给出的 ISBN 校验位无效，已丢弃：{value}"})
                value = None
            else:
                value = normalized
        setattr(candidate, name, value)
        candidate.fields[name] = VisionField(
            value=value,
            readable=bool(readable.get(name)) if isinstance(readable.get(name), bool) else None,
        )

    raw_authors = data.get("authors")
    authors: list[str] = []
    if isinstance(raw_authors, list):
        for a in raw_authors[:_MAX_AUTHORS]:
            cleaned, type_error = _clean_text_field(a)
            if type_error:
                warnings.append({"code": "field_type_error", "field": "authors",
                                 "message": f"作者条目类型异常已忽略：{a!r}"})
                continue
            if cleaned:
                authors.append(cleaned)
    elif isinstance(raw_authors, str) and raw_authors.strip():
        warnings.append({"code": "field_type_error", "field": "authors",
                         "message": "authors 应为数组，收到字符串，按单作者处理"})
        cleaned, _ = _clean_text_field(raw_authors)
        if cleaned:
            authors = [cleaned]
    elif raw_authors is not None:
        warnings.append({"code": "field_type_error", "field": "authors",
                         "message": f"authors 字段类型异常已忽略：{raw_authors!r}"})
    candidate.authors = authors
    candidate.fields["authors"] = VisionField(
        value=None, readable=bool(readable.get("authors"))
        if isinstance(readable.get("authors"), bool) else None,
    )
    candidate.confidence = _clamp_confidence(data.get("confidence"))
    candidate.warnings = warnings
    return candidate


def _extract_content(payload) -> str | None:
    """从 OpenAI 兼容响应提取文本内容与 usage。"""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
    return None


def _extract_usage(payload) -> dict | None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    out: dict = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return out


def recognize_cover_fields(
    db: Session,
    image_path: Path,
    *,
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> VisionServiceResult:
    """识别一张封面，返回结构化候选。只生成候选，不写任何数据。"""
    started = time.monotonic()
    row = load_row(db)
    if row is None or not getattr(row, "enabled", False):
        return _fail(ERR_DISABLED, "视觉识别服务未启用（Owner 未在设置页启用模型）")
    if not (row.base_url and row.model_id and row.api_key):
        return _fail(ERR_NOT_CONFIGURED, "视觉识别服务配置不完整（缺接口地址/模型 ID/API Key）")

    try:
        image_bytes = Path(image_path).read_bytes()
    except OSError as exc:
        return _fail(ERR_IMAGE_UNREADABLE, f"无法读取图片：{exc}")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        return _fail(ERR_IMAGE_TOO_LARGE, f"图片超过 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB 上限")
    image_sha = hashlib.sha256(image_bytes).hexdigest()

    fingerprint = model_fingerprint(row)
    cache_root = _cache_dir(cache_dir)
    key = _cache_key(image_sha, fingerprint)
    if use_cache:
        cached = _load_cache(cache_root, key)
        if cached is not None:
            logger.info("视觉识别命中缓存 image_sha=%s… fingerprint=%s",
                        image_sha[:12], fingerprint)
            return cached

    media_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    data_url = f"data:{media_type};base64," + base64.b64encode(image_bytes).decode("ascii")
    url = row.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": row.model_id,
        "max_tokens": row.max_tokens,
        "temperature": row.temperature,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "识别这张图书封面并按系统要求输出 JSON。"},
                {"type": "image_url", "image_url": {"url": data_url, "detail": row.image_detail}},
            ]},
        ],
    }
    # 日志与错误信息只含模型 ID 与指纹，不含 Key 与请求头
    headers = {"Authorization": f"Bearer {row.api_key}"}

    attempts = 0
    last_error: VisionServiceResult | None = None
    while attempts < _MAX_ATTEMPTS:
        attempts += 1
        try:
            status, payload_json, text = _post_chat_completion(
                url, payload, headers, float(row.timeout_seconds))
        except _HttpTimeout:
            last_error = _fail(ERR_TIMEOUT, "模型请求超时", attempts=attempts)
        except _HttpNetwork as exc:
            last_error = _fail(ERR_NETWORK, f"模型请求网络错误：{exc}", attempts=attempts)
        else:
            if status == 200:
                content = _extract_content(payload_json)
                if content is None:
                    last_error = _fail(ERR_INVALID_RESPONSE,
                                       "模型响应缺少可解析的文本内容", attempts=attempts)
                    break  # 响应已完整到达，重试无意义
                try:
                    candidate = parse_model_content(content)
                except ValueError as exc:
                    last_error = _fail(ERR_INVALID_RESPONSE, str(exc), attempts=attempts)
                    break
                result = VisionServiceResult(
                    ok=True, candidate=candidate, message="识别完成",
                    elapsed_ms=round((time.monotonic() - started) * 1000, 1),
                    attempts=attempts, usage=_extract_usage(payload_json),
                    model_fingerprint=fingerprint,
                    image_sha256=image_sha,
                )
                logger.info("视觉识别完成 model=%s fingerprint=%s attempts=%d elapsed_ms=%.0f",
                            row.model_id, fingerprint, attempts, result.elapsed_ms)
                if use_cache:
                    _save_cache(cache_root, key, result)
                return result
            if status in (401, 403):
                # 鉴权错误不重试：重试同样会被拒
                return _fail(ERR_UNAUTHORIZED, f"模型接口鉴权失败（HTTP {status}）",
                             attempts=attempts, model_fingerprint=fingerprint,
                             image_sha256=image_sha)
            if status == 429:
                last_error = _fail(ERR_RATE_LIMITED, "模型接口限流（HTTP 429）",
                                   attempts=attempts)
            elif status >= 500:
                last_error = _fail(ERR_SERVER_ERROR, f"模型接口服务错误（HTTP {status}）",
                                   attempts=attempts)
            else:
                last_error = _fail(ERR_CLIENT_ERROR, f"模型接口拒绝请求（HTTP {status}）：{text[:200]}",
                                   attempts=attempts)
                break  # 其余 4xx 为确定性错误，不重试

        if last_error and last_error.error_code not in _RETRYABLE_ERRORS:
            break
        if attempts < _MAX_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SEC[min(attempts - 1, len(_RETRY_BACKOFF_SEC) - 1)])

    assert last_error is not None
    last_error.elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    last_error.model_fingerprint = fingerprint
    last_error.image_sha256 = image_sha
    if last_error.error_code == ERR_TIMEOUT:
        # 契约 §4.6：结果未知时注明潜在重复计费，不能承诺外部 API 恰好一次
        last_error.message += (
            "；请求可能已到达提供方（潜在重复计费），结果未知，不自动重试由调用方决定"
        )
    logger.warning("视觉识别失败 code=%s model=%s fingerprint=%s attempts=%d",
                   last_error.error_code, row.model_id, fingerprint, attempts)
    return last_error
