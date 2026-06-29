from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TimestampUpdateMixin

if TYPE_CHECKING:
    from app.models.member import Member
    from app.models.tag import BookTag


class Book(Base, TimestampUpdateMixin):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    isbn13: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    isbn10: Mapped[str | None] = mapped_column(String(20), index=True)
    asin: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    subtitle: Mapped[str | None] = mapped_column(String(500))
    original_title: Mapped[str | None] = mapped_column(String(500))
    normalized_title: Mapped[str | None] = mapped_column(String(500), index=True)
    authors: Mapped[str | None] = mapped_column(Text)  # JSON array
    translators: Mapped[str | None] = mapped_column(Text)  # JSON array
    publisher: Mapped[str | None] = mapped_column(String(200))
    publish_date: Mapped[str | None] = mapped_column(String(20))
    edition: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(10))
    page_count: Mapped[int | None] = mapped_column(Integer)
    cover_path: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    average_rating: Mapped[float | None] = mapped_column(Float)
    rating_count: Mapped[int | None] = mapped_column(Integer)
    google_books_id: Mapped[str | None] = mapped_column(String(50))
    openlibrary_id: Mapped[str | None] = mapped_column(String(50))
    goodreads_id: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(50))
    extra: Mapped[str | None] = mapped_column(Text)  # JSON

    copies: Mapped[list[BookCopy]] = relationship(back_populates="book", cascade="all, delete-orphan")
    reading_progress: Mapped[list[ReadingProgress]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    reading_logs: Mapped[list[ReadingLog]] = relationship(back_populates="book", cascade="all, delete-orphan")
    reading_notes: Mapped[list[ReadingNote]] = relationship(back_populates="book", cascade="all, delete-orphan")
    purchase_records: Mapped[list[PurchaseRecord]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    book_tags: Mapped[list[BookTag]] = relationship(back_populates="book", cascade="all, delete-orphan")


class BookCopy(Base, TimestampUpdateMixin):
    __tablename__ = "book_copies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    copy_type: Mapped[str] = mapped_column(String(20), default="physical", nullable=False)
    format: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(200))
    file_path: Mapped[str | None] = mapped_column(String(500))
    owner_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    acquire_type: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="in_shelf", nullable=False)
    condition: Mapped[str | None] = mapped_column(String(50))
    extra: Mapped[str | None] = mapped_column(Text)

    book: Mapped[Book] = relationship(back_populates="copies")
    owner: Mapped[Member | None] = relationship(back_populates="owned_copies")
    purchase_records: Mapped[list[PurchaseRecord]] = relationship(back_populates="copy")


class ReadingProgress(Base):
    __tablename__ = "reading_progress"
    __table_args__ = (UniqueConstraint("book_id", "member_id", name="uq_reading_progress_book_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="unread", nullable=False)
    to_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owned: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    borrowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_page: Mapped[int | None] = mapped_column(Integer)
    percent: Mapped[float | None] = mapped_column(Float)
    personal_notes: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(String(20))
    finish_date: Mapped[str | None] = mapped_column(String(20))
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rating: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    book: Mapped[Book] = relationship(back_populates="reading_progress")
    member: Mapped[Member] = relationship(back_populates="reading_progress")


class ReadingLog(Base, TimestampMixin):
    __tablename__ = "reading_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True)
    log_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    pages_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    minutes_read: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    session_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    book: Mapped[Book] = relationship(back_populates="reading_logs")
    member: Mapped[Member] = relationship(back_populates="reading_logs")


class PurchaseRecord(Base, TimestampMixin):
    __tablename__ = "purchase_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    copy_id: Mapped[int | None] = mapped_column(ForeignKey("book_copies.id", ondelete="SET NULL"))
    purchase_date: Mapped[str | None] = mapped_column(String(10))
    price: Mapped[float | None] = mapped_column(Float)
    original_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="CNY", nullable=False)
    channel: Mapped[str | None] = mapped_column(String(100))
    order_no: Mapped[str | None] = mapped_column(String(100))
    seller: Mapped[str | None] = mapped_column(String(200))
    buyer_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[str | None] = mapped_column(Text)

    book: Mapped[Book] = relationship(back_populates="purchase_records")
    copy: Mapped[BookCopy | None] = relationship(back_populates="purchase_records")
    buyer: Mapped[Member | None] = relationship(back_populates="purchase_records")


class ReadingNote(Base, TimestampUpdateMixin):
    __tablename__ = "reading_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True)
    note_type: Mapped[str] = mapped_column(String(20), default="excerpt", nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    chapter: Mapped[str | None] = mapped_column(String(200))

    book: Mapped[Book] = relationship(back_populates="reading_notes")
    member: Mapped[Member] = relationship(back_populates="reading_notes")
