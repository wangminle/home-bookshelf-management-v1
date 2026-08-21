"""WBS-5 / 权限阶段 2：成员凭据与 Web 会话。

Member 独立凭据（权限基线 §12.1）：
- owner_credentials 演进为 member_credentials——每名 Member 零或一条可撤销凭据；
- 密码摘要 Argon2id；保留失败锁定（5 次/15 分钟）与密码重置/停用后的会话撤销。
Web 会话使用 HttpOnly Cookie。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampUpdateMixin


class MemberCredential(Base, TimestampUpdateMixin):
    """成员登录凭据：一名 Member 零或一条；Argon2id 哈希，含防爆破锁定状态。"""
    __tablename__ = "member_credentials"
    # 唯一约束与命名唯一索引同时声明（与既有建表口径一致）
    __table_args__ = (Index("ix_member_credentials_member_id", "member_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False
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
