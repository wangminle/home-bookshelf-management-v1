import json
import re
import unicodedata
from datetime import date


def is_valid_publish_date(value: str | None) -> bool:
    """校验 publish_date 是否为合法的 YYYY / YYYY-MM / YYYY-MM-DD 真实日期。

    BUG-114：元数据源与 intake 安全网此前只校验格式（正则），不校验真实日期，
    导致 '2024-13-99' 等非法日期可落库并在序列化/统计中持续出错。
    """
    if value is None or value == "":
        return False
    cleaned = str(value).strip()
    if not cleaned:
        return False
    m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", cleaned)
    if not m:
        return False
    year, month, day = m.group(1), m.group(2) or "01", m.group(3) or "01"
    try:
        date.fromisoformat(f"{year}-{month}-{day}")
    except ValueError:
        return False
    return True


def normalize_title(title: str) -> str:
    """归一书名用于去重：NFKC 全/半角统一、去标点、折叠空白、小写。

    例：'Harry  Potter.' / 'Ｈａｒｒｙ Ｐｏｔｔｅｒ' / 'Harry Potter.'
    均归一为 'harry potter'，避免同书因排版差异重复入库。
    """
    if not title:
        return ""
    # NFKC：全角→半角、兼容等价形式统一（如 ﾊ→ハ、①→1）
    text = unicodedata.normalize("NFKC", str(title))
    # 去标点符号（含中英文标点），保留字母数字与空白
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))
    # 折叠连续空白为单个空格
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def normalize_isbn(raw: str | None) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    if not raw:
        return None
    digits = re.sub(r"[^0-9Xx]", "", raw.strip())
    if len(digits) == 10:
        return digits.upper()
    if len(digits) == 13:
        return digits
    return None


def isbn10_to_isbn13(isbn10: str) -> str:
    body = f"978{isbn10[:-1]}"
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
    check = (10 - total % 10) % 10
    return f"{body}{check}"


def canonical_isbn13(raw: str | None) -> str | None:
    """规范化为 ISBN-13；校验位错误时返回 None，避免脏 ISBN 入库。"""
    if not is_valid_isbn(raw):
        return None
    normalized = normalize_isbn(raw)
    if not normalized:
        return None
    if len(normalized) == 13:
        return normalized
    return isbn10_to_isbn13(normalized)


def isbn_lookup_keys(raw: str | None) -> set[str]:
    normalized = normalize_isbn(raw)
    if not normalized:
        return set()
    keys = {normalized}
    if len(normalized) == 10:
        keys.add(isbn10_to_isbn13(normalized))
    return keys


def is_valid_isbn(raw: str | None) -> bool:
    normalized = normalize_isbn(raw)
    if not normalized:
        return False
    if len(normalized) == 10:
        return _isbn10_check(normalized)
    if len(normalized) == 13:
        return _isbn13_check(normalized)
    return False


def _isbn10_check(isbn10: str) -> bool:
    if len(isbn10) != 10:
        return False
    total = 0
    for i, ch in enumerate(isbn10[:-1]):
        if not ch.isdigit():
            return False
        total += int(ch) * (10 - i)
    check_ch = isbn10[-1]
    check_val = 10 if check_ch in ("X", "x") else (int(check_ch) if check_ch.isdigit() else -1)
    if check_val < 0:
        return False
    total += check_val
    return total % 11 == 0


def _isbn13_check(isbn13: str) -> bool:
    if len(isbn13) != 13 or not isbn13.isdigit():
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn13[:-1]))
    return (10 - total % 10) % 10 == int(isbn13[-1])


def author_in_json_list(book_authors_raw: str | None, author: str) -> bool:
    hint = author.strip().lower()
    if not hint:
        return True
    book_authors = deserialize_json_list(book_authors_raw) or []
    return any(name.strip().lower() == hint for name in book_authors)


def sanitize_filename_stem(name: str) -> str:
    cleaned = re.sub(r"[^\w\-.]", "_", name.strip())
    return cleaned[:200] if cleaned else "upload"


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(value: str) -> str:
    return f"%{escape_like(value.strip())}%"


def serialize_json_list(values: list[str] | None) -> str | None:
    if not values:
        return None
    cleaned = [v.strip() for v in values if v and v.strip()]
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def serialize_json_dict(value: dict | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False)


def deserialize_json_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else None
    except json.JSONDecodeError:
        return None


def deserialize_json_dict(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


# 全局书籍状态聚合口径（BUG-117/123）：
# 一本书可能有多位成员同时阅读、处于不同状态。全局仪表盘与列表筛选需统一为"每书一个状态"，
# 使 by_status 合计不超过 total_books，且统计与 GET /books?status=X 结果一致。
# 优先级：有人读完即 finished；否则有人在读即 reading；否则有人弃读/放弃即 abandoned/dropped；
# 其余（含无任何进度记录）一律为 unread。
_STATUS_PRIORITY = ("finished", "reading", "abandoned", "dropped", "unread")
_STATUS_RANK = {s: i for i, s in enumerate(_STATUS_PRIORITY)}


def aggregate_book_status(member_statuses: list[str] | None) -> str:
    """根据一本书在所有成员上的进度状态，聚合出该书的单一全局状态。

    BUG-117/123：统计与筛选共用本口径，保证 by_status 总和 <= total_books，
    且 GET /stats 的 by_status 与 GET /books?status=X 数量一致。
    """
    if not member_statuses:
        return "unread"
    # 取优先级最高（rank 最小）的状态
    best_rank = len(_STATUS_PRIORITY)
    best = "unread"
    for s in member_statuses:
        rank = _STATUS_RANK.get(s)
        if rank is not None and rank < best_rank:
            best_rank = rank
            best = s
    return best