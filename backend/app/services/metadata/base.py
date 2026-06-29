from dataclasses import dataclass, field


@dataclass
class BookMetadata:
    title: str
    subtitle: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    authors: list[str] = field(default_factory=list)
    publisher: str | None = None
    publish_date: str | None = None
    page_count: int | None = None
    language: str | None = None
    category: str | None = None
    summary: str | None = None
    cover_url: str | None = None
    openlibrary_id: str | None = None
    google_books_id: str | None = None
    extra: dict | None = None
    source: str = "manual"


class MetadataProvider:
    name: str = "base"

    def fetch_by_isbn(self, isbn: str) -> BookMetadata | None:
        raise NotImplementedError

    def search(self, title: str, author: str | None = None) -> BookMetadata | None:
        raise NotImplementedError
