from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


NoteType = Literal["excerpt", "review", "thought"]


class NoteCreate(BaseModel):
    member_id: int | None = None
    note_type: NoteType = "excerpt"
    content_md: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=0)
    chapter: str | None = None


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
