from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ReadingStatus = Literal["unread", "reading", "finished", "abandoned", "dropped"]

READING_STATUSES = frozenset({"unread", "reading", "finished", "abandoned", "dropped"})


class ProgressUpdate(BaseModel):
    member_id: int | None = None
    status: ReadingStatus | None = Field(default=None, description="unread | reading | finished | abandoned | dropped")
    current_page: int | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)
    rating: int | None = Field(default=None, ge=1, le=5)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in READING_STATUSES:
            raise ValueError(f"status 必须是 {', '.join(sorted(READING_STATUSES))} 之一")
        return value


class ProgressOut(BaseModel):
    id: int
    book_id: int
    member_id: int
    status: str
    current_page: int | None = None
    percent: float | None = None
    rating: int | None = None
    finish_date: str | None = None
    updated_at: datetime
    message: str
