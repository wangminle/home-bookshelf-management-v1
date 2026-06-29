from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    role: str
    avatar_path: str | None = None
    channel_bindings: dict[str, Any] | None = None
    reading_streak_offset: int
    created_at: datetime
    updated_at: datetime


class MemberBind(BaseModel):
    member_id: int
    channel: str = Field(min_length=1, max_length=30)
    external_user_id: str = Field(min_length=1)
