from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_USER_AGENT = "home-bookshelf/1.0 (+https://github.com/home-bookshelf)"


def get_json(url: str, *, timeout: float = 15, user_agent: str = DEFAULT_USER_AGENT) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError):
        return None


def get_text(url: str, *, timeout: float = 15, user_agent: str = DEFAULT_USER_AGENT) -> str | None:
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
            return raw.decode(encoding, errors="replace")
    except (urllib.error.URLError, TimeoutError):
        return None
