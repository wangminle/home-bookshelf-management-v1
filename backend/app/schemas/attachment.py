from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AttachmentCreate(BaseModel):
    entity_type: Literal["book", "member", "note"] = Field(description="book | member | note")
    entity_id: int
    attach_type: Literal["link", "file", "markdown"] = Field(description="link | file | markdown")
    title: str | None = None
    url: str | None = None
    content_md: str | None = None
    mime_type: str | None = None
    sort_order: int = 0


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: int
    attach_type: str
    title: str | None = None
    url: str | None = None
    file_path: str | None = None
    content_md: str | None = None
    mime_type: str | None = None
    sort_order: int
    created_at: datetime
    message: str = ""
