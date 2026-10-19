"""ISBN 规范化与校验（CLI 侧，与后端 normalize/is_valid 口径一致）。"""
from __future__ import annotations


def _ascii_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _digits(value: str | None) -> str:
    # 只保留 ASCII 数字。str.isdigit() 会把上标 ²、全角 ９ 当成数字，随后 int() 崩溃或把全角原样送出。
    return "".join(ch for ch in str(value or "") if _ascii_digit(ch) or ch in "Xx").upper()


def _valid_isbn10(d: str) -> bool:
    if len(d) != 10 or any(not _ascii_digit(ch) for ch in d[:9]) or d[9] not in "0123456789X":
        return False
    total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(d))
    return total % 11 == 0


def _valid_isbn13(d: str) -> bool:
    if len(d) != 13 or any(not _ascii_digit(ch) for ch in d):
        return False
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(d[:12]))
    return (10 - total % 10) % 10 == int(d[12])


def normalize_isbn(value: str | None) -> str | None:
    """校验通过返回 ISBN-13（ISBN-10 自动换算），否则返回 None。"""
    d = _digits(value)
    if _valid_isbn13(d):
        return d
    if _valid_isbn10(d):
        core = "978" + d[:9]
        total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(core))
        return core + str((10 - total % 10) % 10)
    return None
