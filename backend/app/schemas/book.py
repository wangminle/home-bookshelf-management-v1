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

    @field_validator("publish_date")
    @classmethod
    def _validate_publish_date(cls, v):
        if v is None or v == "":
            return None
        cleaned = str(v).strip()
        if not cleaned:
            return None
        # 允许 YYYY / YYYY-MM / YYYY-MM-DD，并校验真实日期合法性（月13、2月30等拒收）
        import re
        from datetime import date

        m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", cleaned)
        if not m:
            raise ValueError("publish_date 须为 YYYY、YYYY-MM 或 YYYY-MM-DD")
        year, month, day = m.group(1), m.group(2) or "01", m.group(3) or "01"
        try:
            date.fromisoformat(f"{year}-{month}-{day}")
        except ValueError as exc:
            raise ValueError(f"publish_date 不是合法日期：{cleaned}") from exc
        return cleaned


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
    openlibrary_id: str | None = None
    google_books_id: str | None = None
    extra: dict[str, Any] | list[Any] | str | None = None
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