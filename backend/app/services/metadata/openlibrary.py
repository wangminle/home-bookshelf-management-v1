from __future__ import annotations

import re
import urllib.parse

from app.services.metadata.base import BookMetadata, MetadataProvider
from app.services.metadata.http import get_json
from app.utils.book_helpers import isbn10_to_isbn13, normalize_isbn


def _normalize_publish_date(raw: str | None) -> str | None:
    """将 OpenLibrary 的各种日期格式规范化为 YYYY / YYYY-MM / YYYY-MM-DD。

    OpenLibrary 返回的 publish_date 格式不统一，如 "July 2008"、"September 1, 2008"、
    "2008-07-01" 等。提取年份（和月份）转为标准格式，无法解析时返回 None。
    BUG-114：规范化后必须校验为真实日期（拒绝 2024-13-99 等非法值），否则下游序列化会 500。
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    from app.utils.book_helpers import is_valid_publish_date

    # 已经是标准格式——但仍需校验真实日期合法性
    if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", s):
        return s if is_valid_publish_date(s) else None
    # 尝试提取 4 位年份 + 月份名（如 "July 2008"、"Sep 1, 2008"）
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m = re.search(r"([A-Za-z]{3,9})\s+\d{1,2},?\s*(\d{4})", s)
    if m:
        mon = months.get(m.group(1)[:3].lower())
        if mon:
            candidate = f"{m.group(2)}-{mon}"
            return candidate if is_valid_publish_date(candidate) else None
        return m.group(2)
    # 尝试提取 "YYYY Month" 或 "Month YYYY"
    m = re.search(r"(\d{4})", s)
    if m:
        return m.group(1)
    return None


def _get_cover_url(cover: object) -> str | None:
    if isinstance(cover, dict):
        return cover.get("large") or cover.get("medium")
    return None


def _parse_language(languages: list | None) -> str | None:
    if not languages:
        return None
    entry = languages[0]
    if isinstance(entry, dict):
        key = entry.get("key")
        if key:
            code = key.rsplit("/", 1)[-1]
            return code[:10] if code else None
        name = entry.get("name")
        if name:
            return str(name)[:10]
    return None


class OpenLibraryProvider(MetadataProvider):
    name = "openlibrary"

    def __init__(self, user_agent: str | None = None):
        self.user_agent = user_agent or "home-bookshelf/1.0"

    def fetch_by_isbn(self, isbn: str) -> BookMetadata | None:
        normalized = normalize_isbn(isbn)
        if not normalized:
            return None

        isbn10 = normalized if len(normalized) == 10 else None
        isbn13 = normalized if len(normalized) == 13 else isbn10_to_isbn13(normalized)
        lookup_keys = [isbn13]
        if isbn10:
            lookup_keys.append(isbn10)

        for lookup in dict.fromkeys(lookup_keys):
            url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{lookup}&format=json&jscmd=data"
            payload = get_json(url, user_agent=self.user_agent)
            if not isinstance(payload, dict):
                continue

            data = payload.get(f"ISBN:{lookup}")
            if data:
                return self._parse_data(
                    data,
                    isbn13=isbn13,
                    isbn10=isbn10,
                )
        return None

    def search(self, title: str, author: str | None = None) -> BookMetadata | None:
        params: dict[str, str] = {"title": title.strip(), "limit": "1"}
        if author:
            params["author"] = author.strip()
        url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(params)
        payload = get_json(url, user_agent=self.user_agent)
        if not isinstance(payload, dict):
            return None

        docs = payload.get("docs") or []
        if not docs:
            return None

        doc = docs[0]
        isbn_list = doc.get("isbn") or []
        isbn13 = None
        for raw in isbn_list:
            normalized = normalize_isbn(raw)
            if normalized and len(normalized) == 13:
                isbn13 = normalized
                break
        if isbn13:
            fetched = self.fetch_by_isbn(isbn13)
            if fetched:
                return fetched

        authors = doc.get("author_name") or []
        publish_year = doc.get("first_publish_year")
        key = doc.get("key")
        ol_id = str(key).replace("/works/", "") if isinstance(key, str) and key else key or None
        return BookMetadata(
            title=doc.get("title") or title,
            authors=authors,
            publish_date=str(publish_year) if publish_year else None,
            page_count=doc.get("number_of_pages_median"),
            openlibrary_id=ol_id,
            cover_url=f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg" if doc.get("cover_i") else None,
            source=self.name,
        )

    def _parse_data(self, data: dict, isbn13: str, isbn10: str | None) -> BookMetadata:
        authors = [a.get("name") for a in data.get("authors") or [] if isinstance(a, dict) and a.get("name")]
        publishers = data.get("publishers") or []
        subjects = data.get("subjects") or []
        publish_date = None
        if data.get("publish_date"):
            publish_date = _normalize_publish_date(str(data["publish_date"]))
        elif data.get("publish_dates"):
            try:
                publish_date = _normalize_publish_date(str(data["publish_dates"][0]))
            except (IndexError, TypeError):
                publish_date = None

        identifiers = data.get("identifiers") or {}
        if not isinstance(identifiers, dict):
            identifiers = {}
        ol_id = None
        if data.get("key") and isinstance(data["key"], str):
            ol_id = data["key"].split("/")[-1]

        isbn10_val = isbn10
        if not isbn10_val:
            isbn10_list = identifiers.get("isbn_10")
            if isinstance(isbn10_list, list) and isbn10_list:
                cand = isbn10_list[0]
                isbn10_val = cand if isinstance(cand, str) else None

        return BookMetadata(
            title=data.get("title") or "Unknown",
            subtitle=data.get("subtitle"),
            isbn13=isbn13,
            isbn10=isbn10_val,
            authors=authors,
            publisher=publishers[0]["name"] if publishers and isinstance(publishers[0], dict) else (publishers[0] if publishers else None),
            publish_date=publish_date,
            page_count=data.get("number_of_pages"),
            language=_parse_language(data.get("languages")),
            category=subjects[0]["name"] if subjects and isinstance(subjects[0], dict) else (subjects[0] if subjects else None),
            summary=data.get("notes") if isinstance(data.get("notes"), str) else None,
            cover_url=_get_cover_url(data.get("cover")),
            openlibrary_id=ol_id,
            source=self.name,
        )
