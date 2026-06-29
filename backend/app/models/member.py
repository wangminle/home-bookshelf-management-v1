from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampUpdateMixin

if TYPE_CHECKING:
    from app.models.book import BookCopy, PurchaseRecord, ReadingLog, ReadingNote, ReadingProgress
    from app.models.tag import BookTag


class Member(Base, TimestampUpdateMixin):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    avatar_path: Mapped[str | None] = mapped_column(String(500))
    channel_bindings: Mapped[str | None] = mapped_column(Text)  # JSON: {"feishu":"ou_xxx"}
    reading_streak_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    reading_progress: Mapped[list[ReadingProgress]] = relationship(back_populates="member")
    reading_logs: Mapped[list[ReadingLog]] = relationship(back_populates="member")
    reading_notes: Mapped[list[ReadingNote]] = relationship(back_populates="member")
    purchase_records: Mapped[list[PurchaseRecord]] = relationship(back_populates="buyer")
    owned_copies: Mapped[list[BookCopy]] = relationship(back_populates="owner")
