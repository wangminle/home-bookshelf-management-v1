from __future__ import annotations

from app.config import settings
from app.services.metadata.base import BookMetadata, MetadataProvider
from app.services.metadata.google_books import GoogleBooksProvider
from app.services.metadata.nlc import NLCProvider
from app.services.metadata.openlibrary import OpenLibraryProvider
from app.utils.book_helpers import isbn10_to_isbn13, normalize_isbn


def is_chinese_isbn(isbn: str | None) -> bool:
    normalized = normalize_isbn(isbn)
    if not normalized:
        return False
    isbn13 = normalized if len(normalized) == 13 else isbn10_to_isbn13(normalized)
    return isbn13.startswith("9787")


def _build_providers() -> dict[str, MetadataProvider]:
    user_agent = settings.metadata_user_agent
    return {
        "google_books": GoogleBooksProvider(api_key=settings.google_books_api_key, user_agent=user_agent),
        "nlc": NLCProvider(user_agent=user_agent),
        "openlibrary": OpenLibraryProvider(user_agent=user_agent),
    }


def get_primary_provider_names(*, chinese: bool) -> list[str]:
    if chinese:
        return ["nlc", "google_books"]
    return ["google_books"]


def get_auxiliary_provider_names() -> list[str]:
    return ["openlibrary"]


def get_search_fallback_provider_names(*, chinese: bool) -> list[str]:
    if chinese:
        return ["google_books", "nlc", "openlibrary"]
    return ["google_books", "openlibrary"]


def fetch_metadata(
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
) -> BookMetadata | None:
    providers = _build_providers()
    normalized_isbn = normalize_isbn(isbn)
    chinese = is_chinese_isbn(normalized_isbn)

    if normalized_isbn:
        for name in get_primary_provider_names(chinese=chinese):
            result = providers[name].fetch_by_isbn(normalized_isbn)
            if result:
                return result

        for name in get_auxiliary_provider_names():
            result = providers[name].fetch_by_isbn(normalized_isbn)
            if result:
                return result

    if title and title.strip():
        for name in get_search_fallback_provider_names(chinese=chinese or _looks_chinese(title)):
            result = providers[name].search(title.strip(), author)
            if result:
                return result

    return None


def _looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
