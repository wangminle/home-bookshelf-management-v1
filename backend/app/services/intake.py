from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, Member, PurchaseRecord
from app.config import settings
from app.services.metadata import fetch_metadata
from app.services.recognition import recognize_isbn_from_image
from app.services.storage import download_cover, save_uploaded_image
from app.utils.book_helpers import (
    author_in_json_list,
    canonical_isbn13,
    deserialize_json_list,
    is_valid_isbn,
    is_valid_publish_date,
    isbn_lookup_keys,
    normalize_isbn,
    normalize_title,
    serialize_json_dict,
    serialize_json_list,
)
from app.utils.db_errors import rollback_on_integrity
from app.utils.time_helpers import local_today_iso

# BUG-119：无 ISBN 入库的查重-插入竞态保护。
# normalized_title 无数据库唯一约束（可空/多作者同书名），无法靠 IntegrityError 兜底。
# 用进程级锁串行化 find-then-insert 关键区，杜绝同一进程内并发请求同时通过查重并重复建书。
_INTAKE_LOCK = threading.Lock()

# BUG-133：进程级锁挡不住多 worker/多进程部署，且锁必须覆盖到 commit——
# 否则后一个请求在前一个未提交时复查（看不到未提交行）仍会通过查重。
# 因此再加 data_dir 下的跨进程文件锁，并把"复查→插入→副本/购买→commit"整体放入临界区。
if os.name == "nt":  # Windows
    import msvcrt

    @contextlib.contextmanager
    def _cross_process_lock(lock_path: Path) -> Iterator[None]:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as fh:
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:  # POSIX
    import fcntl

    @contextlib.contextmanager
    def _cross_process_lock(lock_path: Path) -> Iterator[None]:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _intake_lock_path() -> Path:
    return settings.data_dir / "locks" / "intake.lock"


def _cleanup_orphan_cover(cover_path: str | None) -> None:
    if not cover_path:
        return
    try:
        path = (settings.data_dir / cover_path).resolve()
        path.relative_to(settings.data_dir.resolve())
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


@dataclass
class IntakeInput:
    isbn: str | None = None
    title: str | None = None
    author: str | None = None
    authors: list[str] | None = None
    image_path: Path | None = None
    price: float | None = None
    channel: str | None = None
    location: str | None = None
    member_id: int | None = None


@dataclass
class IntakeResult:
    action: str
    book: Book
    matched_source: str | None
    isbn_detected: str | None
    message: str
    created_copy: bool = False
    created_purchase: bool = False
    already_exists: bool = False


def _cover_target_for_image(isbn_detected: str | None, image_path: Path) -> str:
    """上传封面落盘文件名：优先 ISBN，其次图片原名 stem。"""
    return canonical_isbn13(isbn_detected) or isbn_detected or image_path.stem


def intake_book(db: Session, payload: IntakeInput) -> IntakeResult:
    _validate_intake(payload)

    isbn_detected: str | None = normalize_isbn(payload.isbn)
    # 手工传入的 ISBN：位数不对或校验位错误均应报错，避免静默丢弃
    if payload.isbn and payload.isbn.strip():
        if not isbn_detected:
            raise ValueError("ISBN 格式无效，须为 10 或 13 位")
        if not is_valid_isbn(isbn_detected):
            raise ValueError("ISBN 校验位不正确")

    has_image = bool(payload.image_path and payload.image_path.exists())

    # 仅做条码识别（查重/元数据需要 ISBN），封面落盘推迟到确认新建/回填时，避免重复入库产生孤儿文件
    if has_image and not isbn_detected:
        isbn_detected = recognize_isbn_from_image(payload.image_path)

    # 条码识别结果同样要校验位，无效则忽略，回退到书名匹配
    if isbn_detected and not is_valid_isbn(isbn_detected):
        isbn_detected = None

    authors = payload.authors or ([payload.author] if payload.author else None)
    metadata = fetch_metadata(isbn=isbn_detected, title=payload.title, author=payload.author)

    if metadata:
        title = (metadata.title or payload.title or "未知书名").strip()[:500]
        subtitle = (metadata.subtitle[:500] if metadata.subtitle else None)
        isbn13, isbn10 = _resolve_isbn_fields(metadata.isbn13, metadata.isbn10, isbn_detected)
        authors = metadata.authors or authors
        publisher = (metadata.publisher[:200] if metadata.publisher else None)
        publish_date = (metadata.publish_date[:20] if metadata.publish_date else None)
        # 安全网：非 YYYY/YYYY-MM/YYYY-MM-DD 格式或非法真实日期（如 2024-13-99）置空，
        # 避免 BookOut 验证失败（BUG-114）
        if publish_date and not is_valid_publish_date(publish_date):
            publish_date = None
        page_count = metadata.page_count if metadata.page_count is not None and metadata.page_count >= 0 else None
        language = (metadata.language[:10] if metadata.language else None)
        category = (metadata.category[:200] if metadata.category else None)
        summary = metadata.summary
        source = metadata.source
        openlibrary_id = metadata.openlibrary_id
        google_books_id = metadata.google_books_id
        extra = serialize_json_dict(metadata.extra)
        cover_url = metadata.cover_url
    else:
        if not payload.title and not isbn_detected:
            raise ValueError("无法识别书籍信息，请提供 ISBN、书名或清晰的书封条码照片")
        title = (payload.title or f"ISBN {isbn_detected}").strip()[:500]
        subtitle = None
        isbn13, isbn10 = _resolve_isbn_fields(None, None, isbn_detected)
        publisher = publish_date = page_count = language = category = summary = None
        source = "manual"
        openlibrary_id = None
        google_books_id = None
        extra = None
        cover_url = None

    existing = _find_existing(db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors)
    if existing:
        cover_backfilled = False
        # 已有书：仅当缺封面时才回填上传图，避免每次重复扫码都落盘孤儿文件
        if has_image and not existing.cover_path:
            saved = save_uploaded_image(
                payload.image_path,
                target_name=_cover_target_for_image(isbn_detected, payload.image_path),
            )
            if saved:
                existing.cover_path = saved
                cover_backfilled = True
        return _handle_existing_book(
            db, existing, payload, metadata, isbn_detected, source, cover_backfilled=cover_backfilled
        )

    cover_path: str | None = None
    if has_image:
        cover_path = save_uploaded_image(
            payload.image_path,
            target_name=_cover_target_for_image(isbn_detected, payload.image_path),
        )
    if not cover_path and cover_url:
        cover_target = isbn13 or normalize_title(title)
        cover_path = download_cover(cover_url, target_name=cover_target)

    # BUG-119 + BUG-133：normalized_title 无 DB 唯一约束，find-then-insert 存在并发竞态。
    # 进程级锁 + 跨进程文件锁串行化"复查→新建→副本/购买→commit"整个关键区：
    # 锁必须覆盖到 commit，否则并发请求在对方未提交时复查仍会通过查重。
    with _INTAKE_LOCK, _cross_process_lock(_intake_lock_path()):
        # 持锁后再次查重：锁外第一次查重到此处之间，另一个请求可能已建好书
        recheck = _find_existing(db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors)
        if recheck:
            recheck_backfilled = False
            # BUG-136：锁外预生成的封面在命中 recheck 时需要清理或复用，避免孤儿文件堆积
            if cover_path:
                if recheck.cover_path:
                    # 已有书已有封面，预生成的是孤儿文件
                    _cleanup_orphan_cover(cover_path)
                else:
                    # 复用预生成封面回填，避免重复生成
                    recheck.cover_path = cover_path
                    recheck_backfilled = True
            elif has_image and not recheck.cover_path:
                saved = save_uploaded_image(
                    payload.image_path,
                    target_name=_cover_target_for_image(isbn_detected, payload.image_path),
                )
                if saved:
                    recheck.cover_path = saved
                    recheck_backfilled = True
            return _handle_existing_book(
                db, recheck, payload, metadata, isbn_detected, source, cover_backfilled=recheck_backfilled
            )

        book = Book(
            title=title.strip(),
            subtitle=subtitle,
            isbn13=isbn13,
            isbn10=isbn10,
            normalized_title=normalize_title(title),
            authors=serialize_json_list(authors),
            publisher=publisher,
            publish_date=publish_date,
            page_count=page_count,
            language=language,
            category=category,
            summary=summary,
            cover_path=cover_path,
            openlibrary_id=openlibrary_id,
            google_books_id=google_books_id,
            extra=extra,
            source=source,
        )
        db.add(book)
        db.flush()

        created_purchase = False
        created_copy = False
        copy_id: int | None = None
        if payload.location:
            copy = BookCopy(
                book_id=book.id,
                copy_type="physical",
                location=payload.location,
                owner_member_id=payload.member_id,
                acquire_type="purchased" if payload.price is not None else None,
                status="in_shelf",
            )
            db.add(copy)
            db.flush()
            copy_id = copy.id
            created_copy = True

        if payload.price is not None:
            _create_purchase(db, book, payload, copy_id=copy_id)
            created_purchase = True

        try:
            db.commit()
        except IntegrityError as exc:
            # BUG-119：并发入库可能导致 find-then-insert 竞态--回滚后重试查找
            db.rollback()
            retry_existing = _find_existing(db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors)
            if retry_existing:
                # BUG-136：命中重试时清理预生成封面，避免孤儿文件
                _cleanup_orphan_cover(cover_path)
                return _handle_existing_book(
                    db, retry_existing, payload, metadata, isbn_detected, source, cover_backfilled=False
                )
            _cleanup_orphan_cover(cover_path)
            raise rollback_on_integrity(db, exc) from exc
    db.refresh(book)

    message = f"已入库《{book.title}》"
    if created_copy:
        message += "，已登记副本"
    if created_purchase:
        message += "，已记录购买"

    return IntakeResult(
        action="created",
        book=book,
        matched_source=source if metadata else "manual",
        isbn_detected=isbn_detected,
        message=message,
        created_copy=created_copy,
        created_purchase=created_purchase,
    )


def _validate_intake(payload: IntakeInput) -> None:
    if payload.price is not None and payload.price <= 0:
        raise ValueError("价格必须大于 0")


def _resolve_isbn_fields(
    meta_isbn13: str | None,
    meta_isbn10: str | None,
    detected: str | None,
) -> tuple[str | None, str | None]:
    # 优先采用扫描/手工 ISBN，防止元数据模糊命中张冠李戴
    isbn13 = canonical_isbn13(detected) or canonical_isbn13(meta_isbn13) or canonical_isbn13(meta_isbn10)
    isbn10 = None
    for candidate in (detected, meta_isbn10):
        normalized = normalize_isbn(candidate)
        if normalized and len(normalized) == 10 and is_valid_isbn(normalized):
            isbn10 = normalized
            break
    return isbn13, isbn10


def _handle_existing_book(
    db: Session,
    existing: Book,
    payload: IntakeInput,
    metadata,
    isbn_detected: str | None,
    source: str | None,
    *,
    cover_backfilled: bool = False,
) -> IntakeResult:
    created_purchase = False
    created_copy = False

    if payload.location:
        copy = BookCopy(
            book_id=existing.id,
            copy_type="physical",
            location=payload.location,
            owner_member_id=payload.member_id,
            acquire_type="purchased" if payload.price is not None else None,
            status="in_shelf",
        )
        db.add(copy)
        db.flush()
        created_copy = True
        copy_id = copy.id
    else:
        copy_id = None

    if payload.price is not None:
        _create_purchase(db, existing, payload, copy_id=copy_id)
        created_purchase = True

    if created_copy or created_purchase or cover_backfilled:
        try:
            db.commit()
        except IntegrityError as exc:
            if cover_backfilled:
                _cleanup_orphan_cover(existing.cover_path)
            raise rollback_on_integrity(db, exc) from exc

    message = f"《{existing.title}》已在书架中"
    if created_copy:
        message += "，已添加新副本"
    if created_purchase:
        message += "，已记录购买"
    if cover_backfilled:
        message += "，已补充封面"

    return IntakeResult(
        action="exists",
        book=existing,
        matched_source=source if metadata else None,
        isbn_detected=isbn_detected,
        message=message,
        created_copy=created_copy,
        created_purchase=created_purchase,
        already_exists=True,
    )


def _find_existing(
    db: Session,
    *,
    isbn13: str | None,
    isbn10: str | None,
    title: str,
    authors: list[str] | None,
) -> Book | None:
    lookup_keys: set[str] = set()
    if isbn13:
        lookup_keys |= isbn_lookup_keys(isbn13)
    if isbn10:
        lookup_keys |= isbn_lookup_keys(isbn10)

    if lookup_keys:
        found = db.scalar(
            select(Book).where(
                or_(Book.isbn13.in_(lookup_keys), Book.isbn10.in_(lookup_keys))
            )
        )
        if found:
            return found

    normalized = normalize_title(title)
    candidates = db.scalars(select(Book).where(Book.normalized_title == normalized)).all()
    if not candidates:
        return None
    if not authors:
        return candidates[0] if len(candidates) == 1 else None

    author_hint = authors[0].strip().lower()
    matched = [book for book in candidates if _authors_match(book.authors, author_hint)]
    return matched[0] if len(matched) == 1 else None


def _authors_match(book_authors_raw: str | None, author_hint: str) -> bool:
    book_authors = deserialize_json_list(book_authors_raw) or []
    if not book_authors:
        return False
    return any(name.strip().lower() == author_hint for name in book_authors)


def _create_purchase(db: Session, book: Book, payload: IntakeInput, copy_id: int | None = None) -> None:
    if payload.member_id is not None:
        member = db.get(Member, payload.member_id)
        if not member:
            raise ValueError(f"成员 ID {payload.member_id} 不存在")

    db.add(
        PurchaseRecord(
            book_id=book.id,
            copy_id=copy_id,
            price=payload.price,
            channel=payload.channel,
            buyer_member_id=payload.member_id,
            purchase_date=local_today_iso(),
        )
    )
