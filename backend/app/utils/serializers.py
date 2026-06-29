from __future__ import annotations

import json

from app.schemas.book import BookOut
from app.schemas.copy import CopyOut
from app.schemas.note import NoteOut
from app.schemas.purchase import PurchaseOut
from app.schemas.reading import ProgressOut
from app.utils.book_helpers import deserialize_json_list


def book_to_out(book) -> BookOut:
    return BookOut(
        id=book.id,
        title=book.title,
        subtitle=book.subtitle,
        isbn13=book.isbn13,
        isbn10=book.isbn10,
        authors=deserialize_json_list(book.authors),
        publisher=book.publisher,
        publish_date=book.publish_date,
        page_count=book.page_count,
        language=book.language,
        category=book.category,
        summary=book.summary,
        cover_path=book.cover_path,
        source=book.source,
        created_at=book.created_at,
        updated_at=book.updated_at,
    )


def copy_to_out(copy) -> CopyOut:
    return CopyOut.model_validate(copy)


def progress_to_out(progress) -> ProgressOut:
    return ProgressOut(
        id=progress.id,
        book_id=progress.book_id,
        member_id=progress.member_id,
        status=progress.status,
        current_page=progress.current_page,
        percent=progress.percent,
        rating=progress.rating,
        finish_date=progress.finish_date,
        updated_at=progress.updated_at,
        message="",
    )


def purchase_to_out(purchase) -> PurchaseOut:
    return PurchaseOut(
        id=purchase.id,
        book_id=purchase.book_id,
        price=purchase.price or 0,
        channel=purchase.channel,
        order_no=purchase.order_no,
        purchase_date=purchase.purchase_date,
        currency=purchase.currency,
        buyer_member_id=purchase.buyer_member_id,
        created_at=purchase.created_at,
        message="",
    )


def note_to_out(note) -> NoteOut:
    return NoteOut.model_validate(note)


def member_bindings(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def book_detail_to_dict(book, *, copies, progress_list, purchases, notes, attachments, tags, custom_fields) -> dict:
    return {
        **book_to_out(book).model_dump(),
        "tags": tags,
        "copies": [copy_to_out(c).model_dump() for c in copies],
        "reading_progress": [progress_to_out(p).model_dump() for p in progress_list],
        "purchase_records": [purchase_to_out(p).model_dump() for p in purchases],
        "reading_notes": [note_to_out(n).model_dump() for n in notes],
        "attachments": [
            {
                "id": a.id,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "attach_type": a.attach_type,
                "title": a.title,
                "url": a.url,
                "file_path": a.file_path,
                "content_md": a.content_md,
                "mime_type": a.mime_type,
                "sort_order": a.sort_order,
                "created_at": a.created_at,
            }
            for a in attachments
        ],
        "custom_fields": [
            {
                "id": f.id,
                "field_key": f.field_key,
                "field_value": f.field_value,
                "value_type": f.value_type,
            }
            for f in custom_fields
        ],
    }
