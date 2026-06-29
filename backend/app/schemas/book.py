from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiResponse(BaseModel):
    ok: bool = True
    data: Any | None = None
    error: str | None = None


class BookBase(BaseModel):
    title: str
    subtitle: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    authors: list[str] | None = None
    publisher: str | None = None
    publish_date: str | None = None
    page_count: int | None = None
    language: str | None = None
    category: str | None = None
    summary: str | None = None


class BookCreate(BookBase):
    pass


class BookUpdate(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    authors: list[str] | None = None
    publisher: str | None = None
    publish_date: str | None = None
    page_count: int | None = None
    language: str | None = None
    category: str | None = None
    summary: str | None = None
    tags: list[str] | None = None


class BookOut(BookBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cover_path: str | None = None
    source: str | None = None
    created_at: datetime
    updated_at: datetime


class BookListOut(BaseModel):
    items: list[BookOut]
    total: int


class HealthOut(BaseModel):
    status: str = "ok"
    app: str
    database: str = "connected"
    google_books_configured: bool = False
    barcode_scan_available: bool = False
