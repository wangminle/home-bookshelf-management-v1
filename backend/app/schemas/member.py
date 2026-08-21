from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# 权限基线 §1.2：登录角色只有 owner/member，不设 guest（BUG-190）
MemberRole = Literal["owner", "member"]


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: MemberRole = Field(default="member")
    avatar_path: str | None = Field(default=None, max_length=500)
    reading_streak_offset: int = Field(default=0, ge=0)
    # 权限阶段 2：登录用户名（省略时按显示名生成唯一值）
    username: str | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, v: str | None) -> str | None:
        # 权限阶段 2：登录输入会 strip，带首尾空白的用户名存储后将无法解析
        return v.strip() or None if v is not None else None

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
    username: str | None = None
    disabled_at: datetime | None = None
    avatar_path: str | None = None
    channel_bindings: dict[str, Any] | None = None
    reading_streak_offset: int
    created_at: datetime
    updated_at: datetime


class MemberUpdateRequest(BaseModel):
    """Owner 成员管理（权限阶段 2）：角色调整与停用/恢复。"""
    role: MemberRole | None = None
    disabled: bool | None = None


class MemberPasswordSetRequest(BaseModel):
    """Owner 重置成员密码（重置后该成员全部会话失效）。"""
    password: str = Field(min_length=8, max_length=128)


class MemberBind(BaseModel):
    member_id: int
    channel: str = Field(min_length=1, max_length=30)
    external_user_id: str = Field(min_length=1)