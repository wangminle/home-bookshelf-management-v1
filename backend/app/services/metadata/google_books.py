from __future__ import annotations

import urllib.parse

from app.services.metadata.base import BookMetadata, MetadataProvider
from app.services.metadata.http import get_json
from app.utils.book_helpers import isbn10_to_isbn13, normalize_isbn


class GoogleBooksProvider(MetadataProvider):
    name = "google_books"

    def __init__(self, api_key: str | None = None, user_agent: str | None = None):
        self.api_key = api_key
        self.user_agent = user_agent

    def fetch_by_isbn(self, isbn: str) -> BookMetadata | None:
        normalized = normalize_isbn(isbn)
        if not normalized:
            return None

        isbn13 = normalized if len(normalized) == 13 else isbn10_to_isbn13(normalized)
        return self._search_volumes(f"isbn:{isbn13}", preferred_isbn=isbn13, isbn10=normalized if len(normalized) == 10 else None)

    def search(self, title: str, author: str | None = None) -> BookMetadata | None:
        query_parts = [f'intitle:"{title.strip()}"']
        if author and author.strip():
            query_parts.append(f'inauthor:"{author.strip()}"')
        return self._search_volumes("+".join(query_parts))

    def _search_volumes(
        self,
        query: str,
        *,
        preferred_isbn: str | None = None,
        isbn10: str | None = None,
    ) -> BookMetadata | None:
        params: dict[str, str] = {"q": query, "maxResults": "3", "printType": "books"}
        if self.api_key:
            params["key"] = self.api_key

        url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(params)
        payload = get_json(url, user_agent=self.user_agent or "home-bookshelf/1.0")
        if not isinstance(payload, dict):
            return None

        items = payload.get("items") or []
        if not items:
            return None

        for item in items:
            parsed = self._parse_volume(item, preferred_isbn=preferred_isbn, isbn10=isbn10)
            if parsed:
                return parsed
        return None

    def _parse_volume(
        self,
        item: dict,
        *,
        preferred_isbn: str | None = None,
        isbn10: str | None = None,
    ) -> BookMetadata | None:
        volume_info = item.get("volumeInfo") or {}
        title = volume_info.get("title")
        if not title:
            return None

        identifiers = volume_info.get("industryIdentifiers") or []
        found_isbn13 = preferred_isbn
        found_isbn10 = isbn10
        for ident in identifiers:
            kind = ident.get("type")
            value = ident.get("identifier")
            if not value:
                continue
            if kind == "ISBN_13" and not found_isbn13:
                found_isbn13 = normalize_isbn(value)
            elif kind == "ISBN_10" and not found_isbn10:
                found_isbn10 = normalize_isbn(value)

        if preferred_isbn and found_isbn13 and found_isbn13 != preferred_isbn:
            return None

        published_date = volume_info.get("publishedDate")
        page_count = volume_info.get("pageCount")
        image_links = volume_info.get("imageLinks") or {}
        cover_url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
        if cover_url and cover_url.startswith("http://"):
            cover_url = "https://" + cover_url[7:]

        categories = volume_info.get("categories") or []
        language = volume_info.get("language")
        if language and len(language) == 2:
            language = f"{language}_CN" if language == "zh" else language

        return BookMetadata(
            title=title,
            subtitle=volume_info.get("subtitle"),
            isbn13=found_isbn13,
            isbn10=found_isbn10,
            authors=volume_info.get("authors") or [],
            publisher=volume_info.get("publisher"),
            publish_date=str(published_date) if published_date else None,
            page_count=int(page_count) if isinstance(page_count, int) else None,
            language=language,
            category=categories[0] if categories else None,
            summary=volume_info.get("description"),
            cover_url=cover_url,
            google_books_id=item.get("id"),
            source=self.name,
        )
