from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Attachment, Book, BookCopy, BookTag, CustomField, PurchaseRecord, ReadingLog, ReadingNote, ReadingProgress, Tag
from app.schemas.book import BookUpdate
from app.utils.book_helpers import canonical_isbn13, isbn_lookup_keys, normalize_isbn, normalize_title, serialize_json_list
from app.utils.db_errors import ConflictError, rollback_on_integrity
from app.utils.serializers import book_detail_to_dict

logger = logging.getLogger(__name__)


@dataclass
class BookUpdateResult:
    book: Book
    message: str


def sync_book_tags(db: Session, book_id: int, tag_names: list[str]) -> list[str]:
    cleaned = list(dict.fromkeys(name.strip() for name in tag_names if name and name.strip()))
    db.execute(delete(BookTag).where(BookTag.book_id == book_id))
    db.flush()

    result: list[str] = []
    for name in cleaned:
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        db.add(BookTag(book_id=book_id, tag_id=tag.id))
        result.append(tag.name)
    return result


def _normalize_updated_isbns(book: Book, payload: BookUpdate, set_fields: set[str] | None = None) -> tuple[str | None, str | None]:
    """根据 payload 更新 ISBN。set_fields 区分未传与显式 null（BUG-118）。"""
    if set_fields is None:
        set_fields = payload.model_fields_set
    next_isbn13 = book.isbn13
    next_isbn10 = book.isbn10

    if "isbn13" in set_fields:
        val = payload.isbn13
        next_isbn13 = (canonical_isbn13(val) or val.strip() or None) if val else None
    if "isbn10" in set_fields:
        val = payload.isbn10
        next_isbn10 = (normalize_isbn(val) or val.strip() or None) if val else None
        derived_isbn13 = canonical_isbn13(next_isbn10) if next_isbn10 else None
        if derived_isbn13:
            next_isbn13 = derived_isbn13

    if next_isbn10 and next_isbn13:
        derived_isbn13 = canonical_isbn13(next_isbn10)
        if derived_isbn13 and derived_isbn13 != next_isbn13:
            raise ConflictError("isbn10 与 isbn13 不一致")

    return next_isbn13, next_isbn10


def _ensure_no_conflicting_isbn(db: Session, *, book_id: int, isbn13: str | None, isbn10: str | None) -> None:
    lookup_keys = isbn_lookup_keys(isbn13) | isbn_lookup_keys(isbn10)
    if not lookup_keys:
        return

    duplicate = db.scalar(
        select(Book).where(
            Book.id != book_id,
            or_(
                Book.isbn13.in_(lookup_keys),
                Book.isbn10.in_(lookup_keys),
            ),
        )
    )
    if duplicate:
        raise ConflictError("书籍已存在（ISBN 冲突）")


def update_book(db: Session, book_id: int, payload: BookUpdate) -> BookUpdateResult:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    # BUG-118：使用 model_fields_set 区分"未传字段"与"显式传 null"
    # 之前 `if payload.X is not None:` 会把显式 null 当成"跳过"，无法清空字段
    set_fields = payload.model_fields_set

    if "title" in set_fields and payload.title is not None:
        book.title = payload.title.strip()
        book.normalized_title = normalize_title(payload.title)
    if "subtitle" in set_fields:
        book.subtitle = payload.subtitle
    if "isbn13" in set_fields or "isbn10" in set_fields:
        next_isbn13, next_isbn10 = _normalize_updated_isbns(book, payload, set_fields)
        _ensure_no_conflicting_isbn(db, book_id=book_id, isbn13=next_isbn13, isbn10=next_isbn10)
        book.isbn13 = next_isbn13
        book.isbn10 = next_isbn10
    if "authors" in set_fields:
        # BUG-118：显式传 null 应清空作者（serialize_json_list(None) -> None）
        book.authors = serialize_json_list(payload.authors)
    if "publisher" in set_fields:
        book.publisher = payload.publisher
    if "publish_date" in set_fields:
        book.publish_date = payload.publish_date
    if "page_count" in set_fields:
        book.page_count = payload.page_count
    if "language" in set_fields:
        book.language = payload.language
    if "category" in set_fields:
        book.category = payload.category
    if "summary" in set_fields:
        book.summary = payload.summary

    if "tags" in set_fields:
        # BUG-118：显式传 null 清空所有标签（sync_book_tags([]) 删除全部 BookTag）
        sync_book_tags(db, book_id, payload.tags or [])

    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(book)
    return BookUpdateResult(book=book, message=f"已更新《{book.title}》")


def get_book_detail(db: Session, book_id: int) -> dict:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    copies = db.scalars(select(BookCopy).where(BookCopy.book_id == book_id)).all()
    progress_list = db.scalars(select(ReadingProgress).where(ReadingProgress.book_id == book_id)).all()
    purchases = db.scalars(select(PurchaseRecord).where(PurchaseRecord.book_id == book_id)).all()
    notes = db.scalars(select(ReadingNote).where(ReadingNote.book_id == book_id)).all()
    attachments = db.scalars(
        select(Attachment).where(Attachment.entity_type == "book", Attachment.entity_id == book_id)
    ).all()
    custom_fields = db.scalars(
        select(CustomField).where(CustomField.entity_type == "book", CustomField.entity_id == book_id)
    ).all()
    tag_rows = db.scalars(
        select(Tag.name).join(BookTag, BookTag.tag_id == Tag.id).where(BookTag.book_id == book_id)
    ).all()

    return book_detail_to_dict(
        book,
        copies=copies,
        progress_list=progress_list,
        purchases=purchases,
        notes=notes,
        attachments=attachments,
        tags=list(tag_rows),
        custom_fields=custom_fields,
    )


# --- 删除与合并（BUG-147 / BUG-148）----------------------------------------------


def _delete_data_file(rel_path: str | None) -> None:
    """删除 data_dir 下的文件（封面/附件等），容忍不存在/越界路径。"""
    if not rel_path:
        return
    try:
        path = (settings.data_dir / rel_path).resolve()
        path.relative_to(settings.data_dir.resolve())
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        # 文件缺失或路径越界：静默跳过，不阻断删除
        pass


def delete_book(db: Session, book_id: int) -> str:
    """删除一本书及其全部关联数据。

    BUG-147：此前整个 API 没有 DELETE 端点，录错/录重只能降级到直接操作 SQLite。
    - 硬外键关联（copies/progress/logs/notes/purchases/book_tags）由 ORM cascade=all,delete-orphan
      + 数据库 ondelete=CASCADE 自动清理，db.delete(book) 即可。
    - 软关联（Attachment/CustomField 用 entity_type+entity_id，无外键）必须手动删，否则留孤儿。
    - 封面文件需手动 unlink。
    """
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    title = book.title
    cover_path = book.cover_path

    # 软关联先清（无外键级联，否则会残留指向已删 book 的行）。
    # 附件的实体文件（Attachment.file_path）需在删行前收集路径，提交后清理，否则留孤儿文件。
    attachment_file_paths = db.scalars(
        select(Attachment.file_path).where(
            Attachment.entity_type == "book", Attachment.entity_id == book_id
        )
    ).all()
    db.execute(
        delete(Attachment).where(Attachment.entity_type == "book", Attachment.entity_id == book_id)
    )
    db.execute(
        delete(CustomField).where(
            CustomField.entity_type == "book", CustomField.entity_id == book_id
        )
    )

    db.delete(book)
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc

    # 提交成功后再删封面与附件文件：避免回滚后文件已丢却书还在
    _delete_data_file(cover_path)
    for fp in attachment_file_paths:
        _delete_data_file(fp)
    return title


@dataclass
class MergeResult:
    target: Book
    source_title: str
    migrated_copies: int
    migrated_purchases: int
    migrated_progress: int
    migrated_notes: int
    migrated_logs: int
    migrated_attachments: int
    migrated_custom_fields: int
    merged_tags: list[str]


def merge_books(db: Session, *, target_id: int, source_id: int) -> MergeResult:
    """把 source 书合并进 target 书，迁移所有关联数据后删除 source。

    BUG-148：此前没有合并接口，重复书只能手动拼字段+删行。本接口处理：
    - 副本/购买/阅读笔记/阅读日志：直接改 book_id 指向 target（无唯一约束，安全迁移）。
    - 阅读进度：有 uq_reading_progress_book_member，target 已有同成员进度时丢弃 source 行（保留 target）。
    - 标签：去重后合并到 target（uq_book_tags_book_tag 约束）。
    - 附件/自定义字段：改 entity_id 指向 target；CustomField 有 uq_custom_fields_entity_key，
      target 已有同 key 时丢弃 source 行。
    - target 缺的字段（ISBN/封面/简介等）用 source 回填。
    - 最后删 source（含其软关联与封面文件，避免残留）。
    """
    if target_id == source_id:
        raise ValueError("目标书与源书不能相同")

    target = db.get(Book, target_id)
    source = db.get(Book, source_id)
    if not target:
        raise ValueError(f"目标书籍 ID {target_id} 不存在")
    if not source:
        raise ValueError(f"源书籍 ID {source_id} 不存在")

    source_title = source.title
    migrated_copies = migrated_purchases = migrated_progress = migrated_notes = 0
    migrated_logs = migrated_attachments = migrated_custom_fields = 0

    # 1. 先快照 source 待回填的字段——source 行随后会被删除，回填在删除后进行。
    # books.isbn13 有 UNIQUE 约束：若 source 还持有该 ISBN 时给 target 设同值，
    # 同一 flush 会撞唯一约束。因此必须先删 source 行、再回填 target。
    source_snapshot = {
        attr: getattr(source, attr)
        for attr in (
            "isbn13",
            "isbn10",
            "cover_path",
            "subtitle",
            "authors",
            "publisher",
            "publish_date",
            "page_count",
            "language",
            "category",
            "summary",
            "openlibrary_id",
            "google_books_id",
        )
    }

    # 2. 副本：直接迁移
    source_copies = db.scalars(select(BookCopy).where(BookCopy.book_id == source_id)).all()
    for copy in source_copies:
        copy.book_id = target_id
        migrated_copies += 1

    # 3. 阅读进度：处理 uq_reading_progress_book_member
    target_progress_members = set(
        db.scalars(select(ReadingProgress.member_id).where(ReadingProgress.book_id == target_id)).all()
    )
    source_progress = db.scalars(
        select(ReadingProgress).where(ReadingProgress.book_id == source_id)
    ).all()
    for prog in source_progress:
        if prog.member_id in target_progress_members:
            # target 已有该成员进度，丢弃 source 这条（保留 target）
            db.delete(prog)
        else:
            prog.book_id = target_id
            target_progress_members.add(prog.member_id)
            migrated_progress += 1

    # 4. 阅读笔记：直接迁移
    source_notes = db.scalars(select(ReadingNote).where(ReadingNote.book_id == source_id)).all()
    for note in source_notes:
        note.book_id = target_id
        migrated_notes += 1

    # 4b. 阅读日志：直接迁移。
    # reading_logs.book_id 配置了 ondelete=CASCADE，若不迁移，删 source 时全部历史会被级联删除。
    source_logs = db.scalars(select(ReadingLog).where(ReadingLog.book_id == source_id)).all()
    for log in source_logs:
        log.book_id = target_id
        migrated_logs += 1

    # 5. 购买记录：直接迁移
    source_purchases = db.scalars(
        select(PurchaseRecord).where(PurchaseRecord.book_id == source_id)
    ).all()
    for purchase in source_purchases:
        purchase.book_id = target_id
        migrated_purchases += 1

    # 6. 标签：去重合并（uq_book_tags_book_tag）。
    # 用 SQL 批量操作而非 ORM 对象改属性——BookTag 有 ondelete=CASCADE + delete-orphan，
    # 若经 ORM 把 source 的 BookTag.book_id 改成 target_id，db.delete(source) 仍会按
    # relationship 集合把它们当 source 的孤儿级联删除。改为：收集 source 的 tag_id，
    # 删掉 source 全部 BookTag 行，再给 target 补它缺失的。
    target_tag_ids = set(
        db.scalars(select(BookTag.tag_id).where(BookTag.book_id == target_id)).all()
    )
    source_tag_ids = set(
        db.scalars(select(BookTag.tag_id).where(BookTag.book_id == source_id)).all()
    )
    db.execute(delete(BookTag).where(BookTag.book_id == source_id))
    for tag_id in source_tag_ids:
        if tag_id not in target_tag_ids:
            db.add(BookTag(book_id=target_id, tag_id=tag_id))
            target_tag_ids.add(tag_id)

    # 7. 附件：改 entity_id
    source_attachments = db.scalars(
        select(Attachment).where(Attachment.entity_type == "book", Attachment.entity_id == source_id)
    ).all()
    for att in source_attachments:
        att.entity_id = target_id
        migrated_attachments += 1

    # 8. 自定义字段：处理 uq_custom_fields_entity_key
    target_field_keys = set(
        db.scalars(
            select(CustomField.field_key).where(
                CustomField.entity_type == "book", CustomField.entity_id == target_id
            )
        ).all()
    )
    source_custom_fields = db.scalars(
        select(CustomField).where(
            CustomField.entity_type == "book", CustomField.entity_id == source_id
        )
    ).all()
    for cf in source_custom_fields:
        if cf.field_key in target_field_keys:
            db.delete(cf)
        else:
            cf.entity_id = target_id
            target_field_keys.add(cf.field_key)
            migrated_custom_fields += 1

    # 9. 删 source 行。
    # 关键：必须用 SQL delete 而非 db.delete(source)——source 的硬关联（copies/progress/
    # logs/notes/purchases/book_tags）有 ORM cascade="all, delete-orphan" + DB ondelete=CASCADE，
    # 若用 db.delete(source)，ORM 会把"已迁到 target（book_id 改成 target_id）但仍在 source
    # relationship 集合里"的对象当孤儿级联删除，导致迁移数据丢失。
    # 用 SQL delete 让删除走 DB 层：此时关联对象的 book_id 已 flush 落库为 target_id，
    # 不再属于 source，DB cascade 不会触及它们。
    # 同时 source 必须在回填 target ISBN 之前真正删除（flush），释放唯一槽位。
    source_cover = source_snapshot["cover_path"]
    db.flush()  # 先把 2-8 步的 book_id/entity_id 更新落库
    db.execute(delete(Book).where(Book.id == source_id))
    db.flush()  # 确保 source 行已从库中移除

    # 10. source 已删，安全回填 target 缺字段（ISBN 冲突此时只可能来自第三方书）
    _backfill_target_from_snapshot(db, target, source_snapshot)

    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc

    # source 封面若被 target 回填接管（_backfill_target_from_snapshot 置空了快照里的 cover_path），
    # 则文件仍被 target 使用，保留。但若 target 本来就与 source 引用同一 cover_path
    # （target 已有封面不触发回填，快照保持非空），直接删会误删 target 正在引用的文件。
    # 因此清理前必须确认该路径未被任何书籍（含 target）继续引用。
    if source_snapshot["cover_path"]:
        referenced = db.scalar(select(Book.id).where(Book.cover_path == source_cover).limit(1))
        if not referenced:
            _delete_data_file(source_cover)

    db.refresh(target)
    merged_tags = list(
        db.scalars(
            select(Tag.name).join(BookTag, BookTag.tag_id == Tag.id).where(BookTag.book_id == target_id)
        ).all()
    )

    return MergeResult(
        target=target,
        source_title=source_title,
        migrated_copies=migrated_copies,
        migrated_purchases=migrated_purchases,
        migrated_progress=migrated_progress,
        migrated_notes=migrated_notes,
        migrated_logs=migrated_logs,
        migrated_attachments=migrated_attachments,
        migrated_custom_fields=migrated_custom_fields,
        merged_tags=merged_tags,
    )


def _backfill_target_from_snapshot(db: Session, target: Book, snapshot: dict) -> None:
    """target 缺的字段用 source 快照回填（source 行已删除，ISBN 唯一槽位已释放）。

    ISBN 冲突校验仅排除 target 自身——此时 source 已不在库中，不会自冲撞。
    若第三方书已占用该 ISBN，仍应报 ConflictError。
    """
    if not target.isbn13 and snapshot["isbn13"]:
        _ensure_no_conflicting_isbn(
            db, book_id=target.id, isbn13=snapshot["isbn13"], isbn10=target.isbn10
        )
        target.isbn13 = snapshot["isbn13"]
    if not target.isbn10 and snapshot["isbn10"]:
        _ensure_no_conflicting_isbn(
            db, book_id=target.id, isbn13=target.isbn13, isbn10=snapshot["isbn10"]
        )
        target.isbn10 = snapshot["isbn10"]
    if not target.cover_path and snapshot["cover_path"]:
        target.cover_path = snapshot["cover_path"]
        # 封面归 target 后，调用方不应再删该文件；置空快照里的 cover_path 作为信号
        snapshot["cover_path"] = None
    for attr in (
        "subtitle",
        "authors",
        "publisher",
        "publish_date",
        "page_count",
        "language",
        "category",
        "summary",
        "openlibrary_id",
        "google_books_id",
    ):
        if not getattr(target, attr) and snapshot.get(attr):
            setattr(target, attr, snapshot[attr])


# --- 设置封面（BUG-151）----------------------------------------------------------


def set_book_cover(db: Session, book_id: int, cover_path: str) -> Book:
    """把已落盘的封面文件设为指定书的 cover_path。

    BUG-151：POST /books 不处理封面，封面只能走 intake/recognize，
    且附件与 cover_path 之间无联动——已有附件图无法直接设为封面。
    本函数接受一个相对 data_dir 的封面路径（由 API 层落盘后传入），
    替换 cover_path 并清理旧封面文件。
    """
    book = db.get(Book, book_id)
    if not book:
        raise ValueError(f"书籍 ID {book_id} 不存在")

    old_cover = book.cover_path
    book.cover_path = cover_path
    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc

    # 新封面已生效后清理旧封面文件（避免被新封面覆盖的同名情况）
    if old_cover and old_cover != cover_path:
        _delete_data_file(old_cover)
    db.refresh(book)
    return book
