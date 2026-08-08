from pydantic import BaseModel, Field, field_validator

from app.schemas.book import BookOut
from app.utils.book_helpers import is_valid_isbn, normalize_isbn


class IntakeRequest(BaseModel):
    isbn: str | None = None
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=200)
    price: float | None = Field(default=None, gt=0)
    channel: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    member_id: int | None = None

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


class IntakeOut(BaseModel):
    action: str
    book: BookOut
    matched_source: str | None = None
    isbn_detected: str | None = None
    message: str
    created_copy: bool = False
    created_purchase: bool = False
    already_exists: bool = False


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