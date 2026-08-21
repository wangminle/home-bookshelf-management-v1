"""共享限流服务（权限阶段 1，基线 §3.2/§12.2）。

Public Catalog、REST 高敏端点与后续 MCP 复用同一实现，只以不同的
key 前缀与 limit/window 参数区分 Profile；不在各自模块另建配额系统。

实现说明：
- 固定窗口计数，线程安全；
- 进程内存储：单实例部署（本项目默认形态）足够；多实例部署时必须
  改用共享存储或网关限流（基线 §12.2），接口签名保持不变以便替换。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class _Window:
    __slots__ = ("count", "window_start", "window_seconds")

    def __init__(self, window_start: float, window_seconds: int) -> None:
        self.count = 0
        self.window_start = window_start
        # CHK-071 修复：bucket 记录自己的窗口长度——不同 Profile（REST/MCP）
        # 使用不同窗口时，过期判定与清理按各自窗口执行，互不提前重置
        self.window_seconds = window_seconds


_buckets: dict[str, _Window] = {}
_lock = threading.Lock()
# 防无界增长：超过该数量时顺带清理过期窗口
_PURGE_THRESHOLD = 10_000


def check(key: str, *, limit: int, window_seconds: int = 60) -> RateLimitDecision:
    """检查并计数。允许时 remaining 递减；拒绝时不重复计数。"""
    now = time.monotonic()
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None or now - bucket.window_start >= bucket.window_seconds:
            bucket = _Window(now, window_seconds)
            _buckets[key] = bucket
            if len(_buckets) > _PURGE_THRESHOLD:
                _purge_expired(now)
        if bucket.count >= limit:
            elapsed = now - bucket.window_start
            retry_after = max(1, int(bucket.window_seconds - elapsed) + 1)
            return RateLimitDecision(False, limit, 0, retry_after)
        bucket.count += 1
        remaining = limit - bucket.count
        return RateLimitDecision(True, limit, remaining, 0)


def _purge_expired(now: float) -> None:
    expired = [k for k, v in _buckets.items() if now - v.window_start >= v.window_seconds]
    for k in expired:
        _buckets.pop(k, None)


def is_exceeded(key: str, *, limit: int, window_seconds: int = 60) -> bool:
    """非计数探测：当前窗口是否已达上限（BUG-193 登录防爆破预检用）。"""
    now = time.monotonic()
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None or now - bucket.window_start >= bucket.window_seconds:
            return False
        return bucket.count >= limit


def reset() -> None:
    """清空全部计数（测试用）。"""
    with _lock:
        _buckets.clear()
