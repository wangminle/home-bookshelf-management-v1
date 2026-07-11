from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.book_helpers import is_valid_isbn, normalize_isbn


class ApiResponse(BaseModel):
    ok: bool = True
    data: Any | None = None
    error: str | None = None


class BookBase(BaseModel):
    title: str = Field(max_length=500)
    subtitle: str | None = Field(default=None, max_length=500)
    isbn13: str | None = None
    isbn10: str | None = None
    authors: list[str] | None = None
    publisher: str | None = Field(default=None, max_length=200)
    publish_date: str | None = Field(default=None, max_length=20)
    page_count: int | None = None
    language: str | None = Field(default=None, max_length=10)
    category: str | None = Field(default=None, max_length=200)
    summary: str | None = None

    @field_validator("page_count", mode="before")
    @classmethod
    def _validate_page_count(cls, v):
        if v is None or v == "":
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError("页数必须为整数")
        if n < 0:
            raise ValueError("页数不能为负数")
        return n


class BookCreate(BookBase):
    @field_validator("title")
    @classmethod
    def _validate_title(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("书名不能为空")
        return str(v).strip()

    @field_validator("isbn13", "isbn10", mode="before")
    @classmethod
    def _validate_isbn(cls, v):
        if v is None or v == "":
            return None
        if not is_valid_isbn(v):
            raise ValueError("ISBN 校验位不正确")
        return normalize_isbn(v)


class BookUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    subtitle: str | None = Field(default=None, max_length=500)
    isbn13: str | None = None
    isbn10: str | None = None
    authors: list[str] | None = None
    publisher: str | None = Field(default=None, max_length=200)
    publish_date: str | None = Field(default=None, max_length=20)
    page_count: int | None = None
    language: str | None = Field(default=None, max_length=10)
    category: str | None = Field(default=None, max_length=200)
    summary: str | None = None
    tags: list[str] | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v):
        if v is None:
            return None
        if not str(v).strip():
            raise ValueError("书名不能为空")
        return str(v).strip()

    @field_validator("isbn13", "isbn10", mode="before")
    @classmethod
    def _validate_isbn(cls, v):
        if v is None or v == "":
            return None
        if not is_valid_isbn(v):
            raise ValueError("ISBN 校验位不正确")
        return normalize_isbn(v)

    @field_validator("page_count", mode="before")
    @classmethod
    def _validate_page_count(cls, v):
        if v is None or v == "":
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError("页数必须为整数")
        if n < 0:
            raise ValueError("页数不能为负数")
        return n


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