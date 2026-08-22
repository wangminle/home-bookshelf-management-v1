"""WBS-5：Agent 访问控制数据模型。

包含 agent_clients、agent_grants、agent_tokens 表。
owner_credentials 和 web_sessions 在 web_auth.py 中定义。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TimestampUpdateMixin


class AgentClient(Base, TimestampUpdateMixin):
    """Agent 客户端注册。"""
    __tablename__ = "agent_clients"
    # DB 同时存在唯一约束与命名唯一索引（e5a2b3c4d6f8 建表口径），两者都显式声明
    __table_args__ = (Index("ix_agent_clients_public_id", "public_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    client_type: Mapped[str | None] = mapped_column(String(50))  # codex/openclaw/hermes/custom
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[str | None] = mapped_column(Text)  # allowlist 的非敏感元数据

    grants: Mapped[list[AgentGrant]] = relationship(back_populates="agent_client")


class AgentGrant(Base, TimestampUpdateMixin):
    """Agent 授权：绑定 agent + member + scopes + 有效期。

    权限阶段 1（CHK-073/BUG-197）：
    - data_scope_json：显式数据范围标记（当前试点仅 "household_shared"）。
      NULL = 未声明（历史 Grant）——MCP 等真实数据门控一律拒绝；
    - version：Grant 版本（基线 §12.3），缩权/改范围应递增并重签 Token
      （完整版本-Token 绑定属权限阶段 3，先落字段与默认值）。
    """
    __tablename__ = "agent_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_client_id: Mapped[int] = mapped_column(ForeignKey("agent_clients.id"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
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
    data_scope_json: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)

    agent_client: Mapped[AgentClient] = relationship(back_populates="grants")
    tokens: Mapped[list[AgentToken]] = relationship(back_populates="grant")


class AgentToken(Base):
    """Agent 访问令牌。数据库只保存 SHA-256 摘要，不保存明文。"""
    __tablename__ = "agent_tokens"
    # DB 同时存在唯一约束与命名唯一索引（e5a2b3c4d6f8 建表口径），两者都显式声明
    __table_args__ = (Index("ix_agent_tokens_token_hash", "token_hash", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grant_id: Mapped[int] = mapped_column(ForeignKey("agent_grants.id"), nullable=False, index=True)
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
    # BUG-213：签发时绑定的 grant 版本快照。授权范围（scopes/data_scope）变更时
    # grant.version 递增并吊销旧令牌；verify_token 校验二者一致，防止旧令牌
    # 继承变更后的新范围（或范围回收后"复活"）
    grant_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)

    grant: Mapped[AgentGrant] = relationship(back_populates="tokens")
