"""WBS-5：Owner 凭证与 Web 会话。

Owner 使用 Argon2id 密码，Web 会话使用 HttpOnly Cookie。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, TimestampUpdateMixin


class OwnerCredential(Base, TimestampUpdateMixin):
    """Owner 凭证：Argon2id 哈希。"""
    __tablename__ = "owner_credentials"
    # DB 同时存在唯一约束与命名唯一索引（e5a2b3c4d6f8 建表口径），两者都显式声明
    __table_args__ = (Index("ix_owner_credentials_member_id", "member_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id"), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebSession(Base):
    """Web 会话：Cookie-based, server-side tracked."""
    __tablename__ = "web_sessions"
    __table_args__ = (Index("ix_web_sessions_session_token", "session_token", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
