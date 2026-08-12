"""WBS-5：Agent 访问控制 Pydantic schemas。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── Agent Client ──

_HTML_TAG_RE = re.compile(r"<[^>]+>")

class AgentClientCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    client_type: str | None = Field(None, max_length=50)

    @field_validator("display_name")
    @classmethod
    def reject_html(cls, v: str) -> str:
        """Agent 名称只接受纯文本，拒绝 HTML 标签。"""
        if _HTML_TAG_RE.search(v):
            raise ValueError("Agent 名称不能包含 HTML 标签")
        return v


class AgentClientOut(BaseModel):
    id: int
    public_id: str
    display_name: str
    client_type: str | None = None
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Agent Grant ──

class AgentGrantCreate(BaseModel):
    agent_client_id: int
    member_id: int
    scopes: list[str] = Field(..., min_length=1)
    expires_in_days: int = Field(30, ge=1, le=365)


class AgentGrantOut(BaseModel):
    id: int
    agent_client_id: int
    member_id: int
    scopes: list[str]
    status: str
    expires_at: datetime
    approved_by_member_id: int
    approved_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentGrantUpdate(BaseModel):
    scopes: list[str] | None = None
    status: Literal["active", "revoked"] | None = None


# ── Agent Token ──

class AgentTokenCreate(BaseModel):
    grant_id: int


class AgentTokenOut(BaseModel):
    """Token 创建后的唯一返回——明文只出现一次。"""
    token: str
    token_prefix: str
    grant_id: int
    expires_at: datetime
    issued_at: datetime


class AgentTokenInfo(BaseModel):
    """Token 列表中的信息（不含明文）。"""
    id: int
    grant_id: int
    token_prefix: str
    issued_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Owner Auth ──

class OwnerLoginRequest(BaseModel):
    password: str = Field(..., min_length=1)


class OwnerLoginResponse(BaseModel):
    authenticated: bool
    member_id: int
    member_name: str


class OwnerPasswordSetRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)
    confirm: str = Field(..., min_length=8, max_length=128)


class OwnerSessionOut(BaseModel):
    authenticated: bool
    member_id: int | None = None
    member_name: str | None = None
