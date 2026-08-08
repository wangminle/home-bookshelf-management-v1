from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CopyType = Literal["physical", "digital"]
CopyStatus = Literal["in_shelf", "lent_out", "lost", "damaged", "storage", "discarded"]
AcquireType = Literal["purchased", "gift", "borrowed", "found", "inherited", "other"]

COPY_TYPES = frozenset(("physical", "digital"))
COPY_STATUSES = frozenset(("in_shelf", "lent_out", "lost", "damaged", "storage", "discarded"))
ACQUIRE_TYPES = frozenset(("purchased", "gift", "borrowed", "found", "inherited", "other"))


class CopyCreate(BaseModel):
    copy_type: CopyType = Field(default="physical", description="physical | digital")
    format: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=200)
    file_path: str | None = Field(default=None, max_length=500)
    owner_member_id: int | None = None
    acquire_type: AcquireType | None = None
    status: CopyStatus = Field(default="in_shelf")
    condition: str | None = Field(default=None, max_length=50)


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