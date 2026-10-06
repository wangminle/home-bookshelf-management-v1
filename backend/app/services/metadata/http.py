from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_USER_AGENT = "home-bookshelf/1.0 (+https://github.com/home-bookshelf)"

# RES-006/BI-01：外部请求的观察钩子。get_json/get_text 出于健壮性会把网络
# 异常吞成 None，调用方因此无法区分"无结果"与"网络/限流故障"——这正是
# "source=manual 不是外部请求超时的充分证据"问题的根源。诊断工具
# （scripts/diagnose_metadata_chain.py）通过 listener 观察每次底层请求的
# 真实结局（状态码/异常/耗时），业务路径不注册 listener，行为不变。
_listeners: list = []
_listener_lock = threading.Lock()


def add_response_listener(fn) -> None:
    with _listener_lock:
        _listeners.append(fn)


def clear_response_listeners() -> None:
    with _listener_lock:
        _listeners.clear()


def _notify_request_event(event: dict) -> None:
    with _listener_lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(event)
        except Exception:  # 诊断钩子绝不影响业务请求
            pass


# 事件里的 URL 会被诊断报告原样落盘（scripts/diagnose_metadata_chain.py），
# 而 Google Books 等请求把 API key 放在查询参数里——分享报告即泄露凭据。
# 发布事件前统一把敏感查询参数的值替换为 ***，路径与非敏感参数保留以便诊断。
SENSITIVE_QUERY_PARAMS = frozenset({
    "key", "apikey", "api_key", "access_token", "token", "secret",
    "client_secret", "password", "signature",
})
_QUERY_PARAM_MASK = "***"


def sanitize_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    masked = [
        (name, _QUERY_PARAM_MASK if name.lower() in SENSITIVE_QUERY_PARAMS else value)
        for name, value in pairs
    ]
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urllib.parse.urlencode(masked, safe="*"), parts.fragment,
    ))


def _secret_pattern(secret: str, *, inner_hex: bool = False) -> str:
    """逐字符匹配明文或 UTF-8 百分编码；仅编码的十六进制位忽略大小写。

    明文形态（inner_hex=False）保持大小写敏感、编码形式只匹配单层
    （test_bug288_encoding_regression 锁定该语义）。
    inner_hex=True 用于本身就是编码串的密钥（查询串原始值）：每个字符的
    编码形式允许 % 被反复再编码（% → %25 → %2525 → …），覆盖异常把
    查询串多次再编码后的回显（BUG-296）；串内 %XX 三元组中处于十六进制位
    的字母额外忽略大小写——再编码链路可能把内层十六进制文本小写化或混用
    大小写，而明文密钥的大小写语义不受影响。
    """
    hex_positions: set[int] = set()
    if inner_hex:
        for triplet in re.finditer(r"%[0-9a-fA-F]{2}", secret):
            hex_positions.update((triplet.start() + 1, triplet.start() + 2))
    parts = []
    for idx, char in enumerate(secret):
        # inner_hex：% 的再编码是 %25；%(?:25)* 让单层/双层/更深回显都命中
        percent = r"%(?:25)*" if inner_hex else "%"
        encoded = "".join(f"(?i:{percent}{byte:02x})" for byte in
                          char.encode("utf-8", errors="surrogateescape"))
        if idx in hex_positions and char in "abcdefABCDEF":
            plain = f"(?i:{char})"
        else:
            plain = re.escape(char)
        forms = [encoded, plain]  # %25 优先于明文 %，避免只掩码编码前缀
        if char == " ":
            forms.append(r"\+")  # 表单编码中空格也可写成 +
        parts.append("(?:" + "|".join(forms) + ")")
    return "".join(parts)


def _error_message(exc: BaseException, url: str) -> str:
    """异常文本可能回显完整 URL，同样把其中的敏感参数值抹掉。"""
    msg = f"{exc.__class__.__name__}: {exc}"
    parts = urllib.parse.urlsplit(url)
    # 值 → 是否按编码串处理（串内十六进制字母忽略大小写）
    secrets: dict[str, bool] = {}
    for pair in parts.query.split("&"):
        raw_name, _, raw_value = pair.partition("=")
        name = urllib.parse.unquote_plus(raw_name).lower()
        if not raw_value or name not in SENSITIVE_QUERY_PARAMS:
            continue
        # 同时保护表单解码和 URL 解码的回显；surrogateescape 保留非法 UTF-8
        # 字节，避免 %FF 等原始写法因被替换字符吞掉而漏过脱敏。
        for decoded in (
            urllib.parse.unquote_plus(raw_value),
            urllib.parse.unquote(raw_value),
            urllib.parse.unquote_plus(raw_value, errors="surrogateescape"),
            urllib.parse.unquote(raw_value, errors="surrogateescape"),
        ):
            secrets.setdefault(decoded, False)
        # BUG-296：raw_value 本身也必须入集合——异常若把查询串再编码
        # （% 写成 %25，可反复多层）回显，只有解码值的编码形态匹配不到
        # 再编码串；raw_value 按编码串处理：每个字符的编码形式允许任意
        # 层 %25 嵌套，且内层十六进制字母被小写化/混用的回显同样命中。
        secrets[raw_value] = True
    if not secrets:
        return msg
    # BUG-288：不枚举固定编码写法，每个字符的编码形式可独立变化。
    # 在原文本上收集所有命中（包括不同起点的重叠），合并区间后再掩码。
    # 依次替换或一次 alternation 都可能先吞掉另一密钥的开头、留下尾部。
    spans = []
    for secret, inner_hex in secrets.items():
        if not secret:
            continue
        pattern = "(?=(" + _secret_pattern(secret, inner_hex=inner_hex) + "))"
        spans.extend(match.span(1) for match in re.finditer(pattern, msg))
    if not spans:
        return msg
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    parts = []
    previous = 0
    for start, end in merged:
        parts.extend((msg[previous:start], _QUERY_PARAM_MASK))
        previous = end
    parts.append(msg[previous:])
    return "".join(parts)


def get_json(url: str, *, timeout: float = 15, user_agent: str = DEFAULT_USER_AGENT) -> dict | list | None:
    import time

    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            _notify_request_event(
                {"kind": "json", "url": sanitize_url(url), "status": resp.status,
                 "elapsed_ms": round((time.monotonic() - started) * 1000, 1), "error": None}
            )
            return body
    except urllib.error.HTTPError as exc:
        # HTTPError 也是 URLError 子类，先捕获以保留状态码（429 限流等）
        _notify_request_event(
            {"kind": "json", "url": sanitize_url(url), "status": exc.code,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": _error_message(exc, url)}
        )
        return None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
        _notify_request_event(
            {"kind": "json", "url": sanitize_url(url), "status": None,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": _error_message(exc, url)}
        )
        return None


def get_text(url: str, *, timeout: float = 15, user_agent: str = DEFAULT_USER_AGENT) -> str | None:
    import time

    started = time.monotonic()
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            encoding = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(encoding, errors="replace")
            _notify_request_event(
                {"kind": "text", "url": sanitize_url(url), "status": resp.status,
                 "elapsed_ms": round((time.monotonic() - started) * 1000, 1), "error": None}
            )
            return text
    except urllib.error.HTTPError as exc:
        _notify_request_event(
            {"kind": "text", "url": sanitize_url(url), "status": exc.code,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": _error_message(exc, url)}
        )
        return None
    except (urllib.error.URLError, TimeoutError) as exc:
        _notify_request_event(
            {"kind": "text", "url": sanitize_url(url), "status": None,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": _error_message(exc, url)}
        )
        return None
