from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class PurchaseCreate(BaseModel):
    price: float = Field(..., gt=0)
    original_price: float | None = Field(default=None, gt=0)
    channel: str | None = Field(default=None, max_length=100)
    order_no: str | None = Field(default=None, max_length=100)
    purchase_date: str | None = None
    currency: str = Field(default="CNY", max_length=10)
    member_id: int | None = None
    copy_id: int | None = None
    notes: str | None = None

    @field_validator("purchase_date", mode="before")
    @classmethod
    def _validate_purchase_date(cls, v):
        if v is None or v == "":
            return None
        try:
            date.fromisoformat(str(v))
        except ValueError:
            raise ValueError("purchase_date 必须为 YYYY-MM-DD 格式")
        return str(v)


class PurchaseOut(BaseModel):
    id: int
    book_id: int
    price: float
    original_price: float | None = None
    channel: str | None = None
    order_no: str | None = None
    purchase_date: str | None = None
    currency: str
    buyer_member_id: int | None = None
    created_at: datetime
    message: str