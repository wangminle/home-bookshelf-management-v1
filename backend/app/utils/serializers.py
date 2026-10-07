from __future__ import annotations

import json

from app.schemas.book import BookOut
from app.schemas.copy import CopyOut, CopyPlacementOut
from app.schemas.note import NoteOut
from app.schemas.purchase import PurchaseOut
from app.schemas.reading import ProgressOut
from app.utils.book_helpers import deserialize_json_dict, deserialize_json_list


def book_to_out(book, *, include_visibility: bool = False) -> BookOut:
    """序列化书目。BUG-227：catalog_visibility 仅 Owner 下发（include_visibility）。"""
    return BookOut(
        id=book.id,
        title=book.title,
        subtitle=book.subtitle,
        isbn13=book.isbn13,
        isbn10=book.isbn10,
        catalog_visibility=getattr(book, "catalog_visibility", None) if include_visibility else None,
        authors=deserialize_json_list(book.authors),
        publisher=book.publisher,
        publish_date=book.publish_date,
        page_count=book.page_count,
        language=book.language,
        category=book.category,
        summary=book.summary,
        cover_path=book.cover_path,
        source=book.source,
        openlibrary_id=book.openlibrary_id,
        google_books_id=book.google_books_id,
        extra=deserialize_json_dict(book.extra),
        created_at=book.created_at,
        updated_at=book.updated_at,
    )


def copy_to_out(copy, *, include_placement: bool = False, db=None,
                display_cache: dict | None = None) -> CopyOut:
    """序列化副本。include_placement=True 时回显结构化位置原始值（LOC-05）。

    LOC-08：有结构化定位且传入 db 时实时生成 location_display 路径文本
    （display_cache 在批量序列化时共享去重）；无结构化定位时 location_display
    回退旧 location 文字（LOC-02 冻结契约）。
    """
    out = CopyOut.model_validate(copy)
    # BUG-298：版本独立于是否已定位；同时避免 from_attributes 带出未授权版本。
    out.placement_version = copy.placement_version if include_placement else None
    if include_placement:
        if copy.placement_shelf_id is not None:
            out.placement = CopyPlacementOut(
                shelf_id=copy.placement_shelf_id,
                cell_id=copy.placement_cell_id,
                version=copy.placement_version,
            )
            if db is not None:
                from app.services import storage_queries

                out.location_display = storage_queries.build_location_display(
                    db, copy.placement_shelf_id, copy.placement_cell_id,
                    cache=display_cache)
        else:
            out.location_display = copy.location
    return out


def copy_to_dict(copy, *, include_placement: bool = False, db=None,
                 display_cache: dict | None = None) -> dict:
    """LOC-05 输出隔离：未授权主体的输出中所有位置相关键完全不出现。"""
    data = copy_to_out(copy, include_placement=include_placement,
                       db=db, display_cache=display_cache).model_dump()
    if not include_placement:
        data.pop("placement", None)
        data.pop("placement_version", None)
        data.pop("location_display", None)
    return data


def progress_to_out(progress) -> ProgressOut:
    status = progress.status or "unread"
    if status == "finished":
        message = "已读完"
    elif status == "reading":
        if progress.current_page is not None:
            message = f"在读至第 {progress.current_page} 页"
        elif progress.percent is not None:
            message = f"在读 {progress.percent}%"
        else:
            message = "在读"
    elif status == "abandoned":
        message = "已弃读"
    elif status == "dropped":
        message = "已放弃"
    else:
        message = "未读"
    return ProgressOut(
        id=progress.id,
        book_id=progress.book_id,
        member_id=progress.member_id,
        status=status,
        current_page=progress.current_page,
        percent=progress.percent,
        rating=progress.rating,
        to_read=progress.to_read,
        finish_date=progress.finish_date,
        updated_at=progress.updated_at,
        message=message,
    )


def purchase_to_out(purchase) -> PurchaseOut:
    return PurchaseOut(
        id=purchase.id,
        book_id=purchase.book_id,
        price=purchase.price or 0,
        original_price=purchase.original_price,
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


def book_detail_to_dict(book, *, copies, progress_list, purchases, notes, attachments, tags, custom_fields,
                        include_placement: bool = False, db=None) -> dict:
    display_cache: dict = {}
    return {
        **book_to_out(book).model_dump(),
        "tags": tags,
        "copies": [copy_to_dict(c, include_placement=include_placement,
                                db=db, display_cache=display_cache) for c in copies],
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
