from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampUpdateMixin

if TYPE_CHECKING:
    from app.models.book import Book


class Tag(Base, TimestampUpdateMixin):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    color: Mapped[str | None] = mapped_column(String(20))

    book_tags: Mapped[list[BookTag]] = relationship(back_populates="tag", cascade="all, delete-orphan")


class BookTag(Base):
    __tablename__ = "book_tags"
    __table_args__ = (UniqueConstraint("book_id", "tag_id", name="uq_book_tags_book_tag"),)

    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)

    book: Mapped[Book] = relationship(back_populates="book_tags")
    tag: Mapped[Tag] = relationship(back_populates="book_tags")
