from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, ReadingNote
from app.schemas.note import NoteCreate
from app.utils.db_errors import rollback_on_integrity
from app.utils.member_helpers import resolve_member_id


@dataclass
class NoteResult:
    note: ReadingNote
    book: Book
    message: str


def create_note(db: Session, book_id: int, payload: NoteCreate) -> NoteResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    member_id = resolve_member_id(db, payload.member_id)
    note = ReadingNote(
        book_id=book_id,
        member_id=member_id,
        note_type=payload.note_type,
        content_md=payload.content_md.strip(),
        page=payload.page,
        chapter=payload.chapter,
    )
    db.add(note)
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(note)

    return NoteResult(note=note, book=book, message=f"已为《{book.title}》添加笔记")
