from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


NoteType = Literal["excerpt", "review", "thought"]


class NoteCreate(BaseModel):
    member_id: int | None = None
    note_type: NoteType = "excerpt"
    content_md: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=0)
    chapter: str | None = Field(default=None, max_length=200)

    @field_validator("content_md")
    @classmethod
    def _validate_content(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("笔记内容不能为空")
        return cleaned


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    member_id: int
    note_type: str
    content_md: str
    page: int | None = None
    chapter: str | None = None
    created_at: datetime
    updated_at: datetime
    message: str = ""
