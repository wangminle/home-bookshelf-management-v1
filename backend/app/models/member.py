from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampUpdateMixin

if TYPE_CHECKING:
    from app.models.book import BookCopy, PurchaseRecord, ReadingLog, ReadingNote, ReadingProgress
    from app.models.tag import BookTag


class Member(Base, TimestampUpdateMixin):
    __tablename__ = "members"
    # BUG-204：登录用户名大小写不敏感唯一（lower(username) 表达式唯一索引）——
    # 普通唯一索引下 Zhang/zhang 可并存，登录解析产生歧义
    __table_args__ = (
        Index("ix_members_username", text("lower(username)"), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 权限阶段 2：登录用户名（大小写不敏感比对；显示名 name 独立保留）
    username: Mapped[str | None] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20), default="member", server_default="member", nullable=False)
    avatar_path: Mapped[str | None] = mapped_column(String(500))
    channel_bindings: Mapped[str | None] = mapped_column(Text)  # JSON: {"feishu":"ou_xxx"}
    reading_streak_offset: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # 权限阶段 2：停用时间——非空表示停用（禁止登录、现有会话全部失效）
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_disabled(self) -> bool:
        return self.disabled_at is not None

    reading_progress: Mapped[list[ReadingProgress]] = relationship(back_populates="member")
    reading_logs: Mapped[list[ReadingLog]] = relationship(back_populates="member")
    reading_notes: Mapped[list[ReadingNote]] = relationship(back_populates="member")
    purchase_records: Mapped[list[PurchaseRecord]] = relationship(back_populates="buyer")
    owned_copies: Mapped[list[BookCopy]] = relationship(back_populates="owner")
