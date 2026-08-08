from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AttachmentCreate(BaseModel):
    entity_type: Literal["book", "member", "note", "copy"] = Field(
        description="book | member | note | copy"
    )
    entity_id: int
    attach_type: Literal["link", "file", "markdown"] = Field(description="link | file | markdown")
    title: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    content_md: str | None = None
    mime_type: str | None = Field(default=None, max_length=100)
    sort_order: int = 0

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        cleaned = str(v).strip()
        parsed = urlparse(cleaned)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("url 仅允许 http/https 协议")
        if not parsed.netloc:
            raise ValueError("url 格式无效")
        return cleaned


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
