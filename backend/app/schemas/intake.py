from pydantic import BaseModel, Field, field_validator

from app.schemas.book import BookOut


class IntakeRequest(BaseModel):
    isbn: str | None = None
    title: str | None = None
    author: str | None = None
    price: float | None = Field(default=None, gt=0)
    channel: str | None = None
    location: str | None = None
    member_id: int | None = None

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
