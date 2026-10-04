from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.book import BookOut
from app.utils.book_helpers import is_valid_isbn, normalize_isbn

# BI-03（契约 §5）：可声明确认的字段
_CONFIRMABLE = ("title", "subtitle", "authors", "isbn")


class IntakeRequest(BaseModel):
    isbn: str | None = None
    title: str | None = Field(default=None, max_length=500)
    # BUG-251：副题输入贯通（无元数据/冲突元数据下保留确认副题）
    subtitle: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=200)
    # BI-03：结构化多作者数组端到端保留；author 单字符串仅作兼容输入
    authors: list[str] = Field(default=None, max_length=50)
    price: float | None = Field(default=None, gt=0)
    channel: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    member_id: int | None = None
    # BI-03（契约 §5）：字段策略。缺省 default 保持旧行为。
    field_policy: Literal["default", "prefer_confirmed"] = "default"
    confirmed_fields: list[str] = Field(default=None, max_length=10)

    @field_validator("isbn", mode="before")
    @classmethod
    def _validate_isbn(cls, v):
        if v is None or v == "":
            return None
        if not is_valid_isbn(v):
            raise ValueError("ISBN 校验位不正确")
        return normalize_isbn(v)

    @field_validator("price", mode="before")
    @classmethod
    def reject_non_positive_price(cls, value):
        if value is None:
            return None
        if isinstance(value, (int, float)) and value <= 0:
            raise ValueError("价格必须大于 0")
        return value

    @field_validator("authors")
    @classmethod
    def _clean_authors(cls, v):
        if v is None:
            return None
        # BUG-276：保留 []（显式清空）与 None（未提供）的区别——
        # prefer_confirmed 自动推断按"是否提供"判定确认字段，空列表不可洗成 None
        return [a.strip()[:200] for a in v if a and a.strip()]

    @field_validator("confirmed_fields")
    @classmethod
    def _validate_confirmed_fields(cls, v):
        if v is None:
            return None
        unknown = [f for f in v if f not in _CONFIRMABLE]
        if unknown:
            raise ValueError(f"不可声明确认的字段: {unknown}（允许 {_CONFIRMABLE}）")
        return v


class IntakeWarningOut(BaseModel):
    """BI-02（契约 §2）：结构化警告。code 稳定，CLI/清单/报告共用。"""

    code: str
    message: str
    field: str | None = None
    detail: dict | None = None


class IntakeOut(BaseModel):
    action: str
    book: BookOut
    matched_source: str | None = None
    isbn_detected: str | None = None
    message: str
    created_copy: bool = False
    created_purchase: bool = False
    already_exists: bool = False
    warnings: list[IntakeWarningOut] | None = None


class IsbnRecognizeOut(BaseModel):
    isbn13: str | None
    found: bool
    message: str


class CoverRecognizeOut(BaseModel):
    found: bool
    isbn13: str | None = None
    title: str | None = None
    authors: list[str] | None = None
    publisher: str | None = None
    cover_path: str | None = None
    matched_source: str | None = None
    message: str