from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, Member, PurchaseRecord
from app.schemas.purchase import PurchaseCreate
from app.utils.db_errors import rollback_on_integrity
from app.utils.time_helpers import local_today_iso


@dataclass
class PurchaseResult:
    purchase: PurchaseRecord
    book: Book
    message: str


def create_purchase(db: Session, book_id: int, payload: PurchaseCreate) -> PurchaseResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    if payload.member_id is not None:
        member = db.get(Member, payload.member_id)
        if not member:
            raise ValueError(f"成员 ID {payload.member_id} 不存在")

    if payload.copy_id is not None:
        copy = db.get(BookCopy, payload.copy_id)
        if not copy or copy.book_id != book_id:
            raise ValueError(f"副本 ID {payload.copy_id} 不存在或不属于该书籍")

    purchase = PurchaseRecord(
        book_id=book_id,
        copy_id=payload.copy_id,
        price=payload.price,
        original_price=payload.original_price,
        currency=payload.currency,
        channel=payload.channel,
        order_no=payload.order_no,
        purchase_date=payload.purchase_date or local_today_iso(),
        buyer_member_id=payload.member_id,
        notes=payload.notes,
    )
    db.add(purchase)
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(purchase)

    channel_hint = f"（{payload.channel}）" if payload.channel else ""
    message = f"已为《{book.title}》记录购买：¥{payload.price}{channel_hint}"
    return PurchaseResult(purchase=purchase, book=book, message=message)
