from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CopyCreate(BaseModel):
    copy_type: str = Field(default="physical", description="physical | digital")
    format: str | None = None
    location: str | None = None
    file_path: str | None = None
    owner_member_id: int | None = None
    acquire_type: str | None = None
    status: str = Field(default="in_shelf")
    condition: str | None = None


class CopyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    copy_type: str
    format: str | None = None
    location: str | None = None
    file_path: str | None = None
    owner_member_id: int | None = None
    acquire_type: str | None = None
    status: str
    condition: str | None = None
    created_at: datetime
    updated_at: datetime
