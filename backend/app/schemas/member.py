from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MemberRole = Literal["owner", "member", "guest"]


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: MemberRole = Field(default="member")
    avatar_path: str | None = Field(default=None, max_length=500)
    reading_streak_offset: int = Field(default=0, ge=0)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v):
        if not str(v).strip():
            raise ValueError("成员名称不能为空")
        return str(v).strip()


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