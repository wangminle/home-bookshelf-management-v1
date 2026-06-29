import json
import re


def normalize_title(title: str) -> str:
    return title.strip().lower()


def normalize_isbn(raw: str | None) -> str | None:
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
