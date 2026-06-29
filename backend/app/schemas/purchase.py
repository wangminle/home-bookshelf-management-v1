from datetime import datetime

from pydantic import BaseModel, Field


class PurchaseCreate(BaseModel):
    price: float = Field(..., gt=0)
    original_price: float | None = Field(default=None, gt=0)
    channel: str | None = None
    order_no: str | None = None
    purchase_date: str | None = None
    currency: str = "CNY"
    member_id: int | None = None
    copy_id: int | None = None
    notes: str | None = None


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
