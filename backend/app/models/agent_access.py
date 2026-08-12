"""WBS-5：Agent 访问控制数据模型。

包含 agent_clients、agent_grants、agent_tokens 表。
owner_credentials 和 web_sessions 在 web_auth.py 中定义。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AgentClient(Base, TimestampMixin):
    """Agent 客户端注册。"""
    __tablename__ = "agent_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    client_type: Mapped[str | None] = mapped_column(String(50))  # codex/openclaw/hermes/custom
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[str | None] = mapped_column(Text)  # allowlist 的非敏感元数据

    grants: Mapped[list[AgentGrant]] = relationship(back_populates="agent_client")


class AgentGrant(Base, TimestampMixin):
    """Agent 授权：绑定 agent + member + scopes + 有效期。"""
    __tablename__ = "agent_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_client_id: Mapped[int] = mapped_column(ForeignKey("agent_clients.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    scopes_json: Mapped[str] = mapped_column(Text, nullable=False, server_default="[]")
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active", nullable=False)
    # active / revoked / expired
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent_client: Mapped[AgentClient] = relationship(back_populates="grants")
    tokens: Mapped[list[AgentToken]] = relationship(back_populates="grant")


class AgentToken(Base):
    """Agent 访问令牌。数据库只保存 SHA-256 摘要，不保存明文。"""
    __tablename__ = "agent_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grant_id: Mapped[int] = mapped_column(ForeignKey("agent_grants.id"), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    grant: Mapped[AgentGrant] = relationship(back_populates="tokens")
