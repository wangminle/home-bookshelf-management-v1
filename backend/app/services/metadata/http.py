from __future__ import annotations

import json
import threading
import urllib.error
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


def get_json(url: str, *, timeout: float = 15, user_agent: str = DEFAULT_USER_AGENT) -> dict | list | None:
    import time

    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            _notify_request_event(
                {"kind": "json", "url": url, "status": resp.status,
                 "elapsed_ms": round((time.monotonic() - started) * 1000, 1), "error": None}
            )
            return body
    except urllib.error.HTTPError as exc:
        # HTTPError 也是 URLError 子类，先捕获以保留状态码（429 限流等）
        _notify_request_event(
            {"kind": "json", "url": url, "status": exc.code,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": f"{exc.__class__.__name__}: {exc}"}
        )
        return None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
        _notify_request_event(
            {"kind": "json", "url": url, "status": None,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": f"{exc.__class__.__name__}: {exc}"}
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
                {"kind": "text", "url": url, "status": resp.status,
                 "elapsed_ms": round((time.monotonic() - started) * 1000, 1), "error": None}
            )
            return text
    except urllib.error.HTTPError as exc:
        _notify_request_event(
            {"kind": "text", "url": url, "status": exc.code,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": f"{exc.__class__.__name__}: {exc}"}
        )
        return None
    except (urllib.error.URLError, TimeoutError) as exc:
        _notify_request_event(
            {"kind": "text", "url": url, "status": None,
             "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
             "error": f"{exc.__class__.__name__}: {exc}"}
        )
        return None
