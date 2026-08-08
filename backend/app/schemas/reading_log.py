from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.time_helpers import local_today_iso


class ReadingLogCreate(BaseModel):
    member_id: int | None = None
    log_date: str = Field(description="YYYY-MM-DD")
    pages_read: int = Field(default=0, ge=0)
    minutes_read: int | None = Field(default=None, ge=0)
    notes: str | None = None
    session_start: datetime | None = None
    session_end: datetime | None = None

    @field_validator("log_date")
    @classmethod
    def validate_log_date(cls, value: str) -> str:
        cleaned = value.strip()
        try:
            d = date.fromisoformat(cleaned)
        except ValueError as exc:
            raise ValueError("log_date 须为有效日期，格式 YYYY-MM-DD") from exc
        today = date.fromisoformat(local_today_iso())
        if d > today:
            raise ValueError("log_date 不能是未来日期")
        return cleaned

    @model_validator(mode="after")
    def validate_session_range(self) -> "ReadingLogCreate":
        if self.session_start and self.session_end and self.session_end < self.session_start:
            raise ValueError("session_end 不能早于 session_start")
        return self


class ReadingLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    member_id: int
    log_date: str
    pages_read: int
    minutes_read: int | None = None
    notes: str | None = None
    session_start: datetime | None = None
    session_end: datetime | None = None
    created_at: datetime
    message: str = ""
