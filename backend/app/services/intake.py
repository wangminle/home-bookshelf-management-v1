from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, BookCopy, Member, PurchaseRecord
from app.config import settings
from app.services.metadata import fetch_metadata
from app.services.recognition import (
    SCAN_NOT_FOUND,
    SCAN_OK,
    SCAN_TIMEOUT,
    SCAN_UNAVAILABLE,
    scan_isbn_from_image,
)
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
    subtitle: str | None = None
    author: str | None = None
    authors: list[str] | None = None
    image_path: Path | None = None
    price: float | None = None
    channel: str | None = None
    location: str | None = None
    member_id: int | None = None
    # BI-03（契约 §5）：字段策略。缺省 default 保持既有行为（元数据可改写展示字段），
    # 旧客户端零改动兼容；prefer_confirmed 时确认字段优先，元数据只补空。
    field_policy: str = "default"
    confirmed_fields: list[str] | None = None


# BI-02（契约 §2）：入库警告码。warnings 为可选字段，旧客户端可忽略。
WARNING_BARCODE_UNAVAILABLE = "barcode_dependency_unavailable"
WARNING_BARCODE_TIMEOUT = "barcode_decode_timeout"
WARNING_BARCODE_NOT_FOUND = "barcode_not_found"
WARNING_BARCODE_INVALID_CHECKSUM = "barcode_invalid_checksum"
WARNING_METADATA_MISSING = "metadata_missing"
# BI-03
WARNING_METADATA_FIELD_CONFLICT = "metadata_field_conflict"
WARNING_ISBN_CONFLICT = "isbn_ownership_conflict"

FIELD_POLICY_DEFAULT = "default"
FIELD_POLICY_PREFER_CONFIRMED = "prefer_confirmed"
# 可声明确认的字段（契约 §5：title、subtitle、authors、isbn 分字段处理；
# BUG-251：subtitle 已有请求输入通道（IntakeInput.subtitle，M3 工作台贯通））
CONFIRMABLE_FIELDS = ("title", "subtitle", "authors", "isbn")


@dataclass
class IntakeWarning:
    """结构化警告：code 稳定（与 CLI/清单/报告共用），message 人读。"""

    code: str
    message: str
    field: str | None = None
    detail: dict | None = None

    def to_dict(self) -> dict:
        out: dict = {"code": self.code, "message": self.message}
        if self.field is not None:
            out["field"] = self.field
        if self.detail is not None:
            out["detail"] = self.detail
        return out


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
    warnings: list[IntakeWarning] | None = None


def _cover_target_for_image(isbn_detected: str | None, image_path: Path) -> str:
    """上传封面落盘文件名：优先 ISBN，其次图片原名 stem。"""
    return canonical_isbn13(isbn_detected) or isbn_detected or image_path.stem


def intake_book(db: Session, payload: IntakeInput, *,
                finalize: Callable[[IntakeResult], None] | None = None) -> IntakeResult:
    """入库；工作流可在最终提交前写入回执，两者共享同一事务。

    finalize 抛出异常时回滚书目/副本/购买与回执，调用者不能在回执失败后
    留下已提交的业务副作用。普通入口保持原有自动提交契约。
    """
    _validate_intake(payload)

    warnings: list[IntakeWarning] = []

    isbn_detected: str | None = normalize_isbn(payload.isbn)
    # 手工传入的 ISBN：位数不对或校验位错误均应报错，避免静默丢弃
    if payload.isbn and payload.isbn.strip():
        if not isbn_detected:
            raise ValueError("ISBN 格式无效，须为 10 或 13 位")
        if not is_valid_isbn(isbn_detected):
            raise ValueError("ISBN 校验位不正确")

    has_image = bool(payload.image_path and payload.image_path.exists())

    # 仅做条码识别（查重/元数据需要 ISBN），封面落盘推迟到确认新建/回填时，避免重复入库产生孤儿文件。
    # BI-02（契约 §4.1）：条码故障不再阻塞入库——有书名/作者线索时降级继续并返回
    # 结构化警告；只有图片且无法解码时在下方"无法识别书籍信息"处拒绝，不建"未知书名"。
    if has_image and not isbn_detected:
        scan = scan_isbn_from_image(payload.image_path)  # 图片损坏抛 ValueError，如实 400
        if scan.outcome == SCAN_OK:
            isbn_detected = scan.isbn
            # 保险：识别层已过滤非法码，这里再核一次校验位（契约 barcode_invalid_checksum）
            if isbn_detected and not is_valid_isbn(isbn_detected):
                warnings.append(IntakeWarning(
                    WARNING_BARCODE_INVALID_CHECKSUM,
                    f"条码解码结果校验位无效，已丢弃：{isbn_detected}",
                ))
                isbn_detected = None
        elif scan.outcome == SCAN_UNAVAILABLE:
            warnings.append(IntakeWarning(
                WARNING_BARCODE_UNAVAILABLE,
                f"{scan.message}；本次按书名/手工信息降级入库",
            ))
        elif scan.outcome == SCAN_TIMEOUT:
            warnings.append(IntakeWarning(WARNING_BARCODE_TIMEOUT, scan.message))
        else:
            warnings.append(IntakeWarning(
                WARNING_BARCODE_NOT_FOUND,
                scan.message or "图片中未发现可解码的 ISBN 条码",
            ))

    # BUG-276：authors=[] 是显式清空（prefer_confirmed 下不从元数据回填），
    # 不得被 or 回退吞掉；None 才表示未提供、可回退到兼容单字符串 author。
    authors = payload.authors if payload.authors is not None else (
        [payload.author] if payload.author else None)
    # BUG-163：保存原始输入书名/作者，用于去重和 normalized_title。
    # 元数据可能改写书名（如 OpenLibrary 返回英文译名），导致同一输入因元数据
    # 命中/超时差异而绕过去重。normalized_title 基于原始输入确保去重键稳定。
    original_title = payload.title.strip()[:500] if payload.title else None
    original_authors = authors
    metadata = fetch_metadata(isbn=isbn_detected, title=payload.title, author=payload.author)

    prefer = payload.field_policy == FIELD_POLICY_PREFER_CONFIRMED
    confirmed = _confirmed_field_names(payload)

    if metadata:
        meta_title = (metadata.title or "").strip() or None
        if prefer and "title" in confirmed and payload.title:
            # BI-03（契约 §5）：确认书名优先，不被外部元数据覆盖；冲突可见
            title = payload.title.strip()[:500]
            if meta_title and meta_title[:500] != title:
                warnings.append(_field_conflict_warning("title", title, meta_title[:500], metadata.source))
        else:
            title = (metadata.title or payload.title or "未知书名").strip()[:500]
        isbn13, isbn10 = _resolve_isbn_fields(metadata.isbn13, metadata.isbn10, isbn_detected)
        meta_authors = metadata.authors or None
        if prefer and "authors" in confirmed:
            # 确认作者保留结构化数组；外部作者仅作冲突证据记录。
            # BUG-276：显式确认的 authors=[] 是有意清空，与"未提供"不同（同
            # _confirmed_field_names 的显式空列表语义），不得被元数据回填。
            if meta_authors and not _same_author_set(meta_authors, authors or []):
                warnings.append(_field_conflict_warning("authors", authors, meta_authors, metadata.source))
        else:
            authors = meta_authors or authors
        if prefer and "isbn" in confirmed and isbn_detected:
            meta_isbn13 = canonical_isbn13(metadata.isbn13) or canonical_isbn13(metadata.isbn10)
            if meta_isbn13 and meta_isbn13 != canonical_isbn13(isbn_detected):
                warnings.append(_field_conflict_warning(
                    "isbn", isbn_detected, meta_isbn13, metadata.source))
        publisher = (metadata.publisher[:200] if metadata.publisher else None)
        publish_date = (metadata.publish_date[:20] if metadata.publish_date else None)
        # BUG-251：确认副题贯通——prefer_confirmed 下确认副题不被外部元数据覆盖，
        # 冲突可见（与 title/authors/isbn 同口径）
        # BUG-276：显式确认的空副题（''）是有意清空，与"未提供"不同，
        # 不得因非空判断被跳过而让元数据回填
        if prefer and "subtitle" in confirmed:
            subtitle = (payload.subtitle or "").strip()[:500] or None
            meta_subtitle = (metadata.subtitle[:500] if metadata.subtitle else None)
            if meta_subtitle and meta_subtitle != subtitle:
                warnings.append(_field_conflict_warning(
                    "subtitle", subtitle, meta_subtitle, metadata.source))
        else:
            subtitle = (metadata.subtitle[:500] if metadata.subtitle else None)
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
        # BI-02（契约 §2）：外部元数据未命中（含外部依赖故障导致的未命中）如实可见，
        # 不把 manual 静默当作"外部无此书"。
        warnings.append(IntakeWarning(
            WARNING_METADATA_MISSING,
            "外部元数据未命中（可能为无结果或外部依赖故障），本次按手工输入信息入库",
        ))
        title = (payload.title or f"ISBN {isbn_detected}").strip()[:500]
        # BUG-251：无外部元数据时保留输入副题（确认副题贯通）
        subtitle = (payload.subtitle or "").strip()[:500] or None
        isbn13, isbn10 = _resolve_isbn_fields(None, None, isbn_detected)
        publisher = publish_date = page_count = language = category = summary = None
        source = "manual"
        openlibrary_id = None
        google_books_id = None
        extra = None
        cover_url = None

    # BI-03：ISBN 归属冲突阻断——命中已有书但书名/作者明显不一致时不自动绑定
    _check_isbn_ownership(
        db, isbn13=isbn13, isbn10=isbn10, title=title,
        original_title=original_title, original_authors=original_authors,
    )

    existing = _find_existing_dedup(
        db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors,
        original_title=original_title, original_authors=original_authors,
    )
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
            db, existing, payload, metadata, isbn_detected, source,
            cover_backfilled=cover_backfilled, warnings=warnings, finalize=finalize,
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
        # BUG-119：结束当前事务以获取最新快照，否则 SQLite 的快照隔离
        # 会导致 recheck 看不到其他 session 已提交的新书（锁外预查询
        # 开启了隐式事务，持锁后仍是同一快照）。
        db.commit()
        # BI-03：并发窗口内可能有同 ISBN 但明显不一致的书落库，持锁后复核归属
        try:
            _check_isbn_ownership(
                db, isbn13=isbn13, isbn10=isbn10, title=title,
                original_title=original_title, original_authors=original_authors,
            )
        except ValueError:
            _cleanup_orphan_cover(cover_path)
            raise
        # 持锁后再次查重：锁外第一次查重到此处之间，另一个请求可能已建好书
        recheck = _find_existing_dedup(
            db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors,
            original_title=original_title, original_authors=original_authors,
        )
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
                db, recheck, payload, metadata, isbn_detected, source,
                cover_backfilled=recheck_backfilled, warnings=warnings, finalize=finalize,
            )

        book = Book(
            title=title.strip(),
            subtitle=subtitle,
            isbn13=isbn13,
            isbn10=isbn10,
            # BUG-163：normalized_title 基于原始输入书名（稳定），而非元数据书名（波动）。
            # 确保同一输入无论元数据命中或超时，去重键始终一致。
            normalized_title=normalize_title(original_title or title),
            # BUG-171：作者同样存原始输入值作为稳定去重锚点；仅无输入作者
            # （如仅 ISBN 入库）时才用元数据作者。代价是展示作者不再富化为
            # 元数据值——换取同书名不同作者的两本书不被宽松回退误合并。
            authors=serialize_json_list(original_authors or authors),
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

        message = f"已入库《{book.title}》"
        if created_copy:
            message += "，已登记副本"
        if created_purchase:
            message += "，已记录购买"
        result = IntakeResult(
            action="created", book=book,
            matched_source=source if metadata else "manual", isbn_detected=isbn_detected,
            message=message, created_copy=created_copy, created_purchase=created_purchase,
            warnings=warnings,
        )
        try:
            db.flush()
            if finalize is not None:
                finalize(result)
            db.commit()
        except IntegrityError as exc:
            # BUG-119：并发入库可能导致 find-then-insert 竞态--回滚后重试查找
            db.rollback()
            retry_existing = _find_existing_dedup(
                db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors,
                original_title=original_title, original_authors=original_authors,
            )
            if retry_existing:
                # BUG-136：命中重试时清理预生成封面，避免孤儿文件
                _cleanup_orphan_cover(cover_path)
                return _handle_existing_book(
                    db, retry_existing, payload, metadata, isbn_detected, source,
                    cover_backfilled=False, warnings=warnings, finalize=finalize,
                )
            _cleanup_orphan_cover(cover_path)
            raise rollback_on_integrity(db, exc) from exc
        except Exception:
            db.rollback()
            _cleanup_orphan_cover(cover_path)
            raise
    db.refresh(book)
    return result


def _validate_intake(payload: IntakeInput) -> None:
    if payload.price is not None and payload.price <= 0:
        raise ValueError("价格必须大于 0")
    if payload.field_policy not in (FIELD_POLICY_DEFAULT, FIELD_POLICY_PREFER_CONFIRMED):
        raise ValueError(f"未知的字段策略: {payload.field_policy}")


def _confirmed_field_names(payload: IntakeInput) -> set[str]:
    """BI-03：确认字段集合。

    显式 confirmed_fields 优先（过滤到可确认字段）；未显式提供列表时，
    prefer_confirmed 下所有已提供的输入字段视为确认——批量脚本核对后的
    整条输入即用户所见值。default 策略恒为空集（保持旧行为）。
    """
    declared = payload.confirmed_fields
    if declared is not None:  # 显式空列表 = 无确认字段（区别于未提供）
        return {f for f in declared if f in CONFIRMABLE_FIELDS}
    if payload.field_policy == FIELD_POLICY_PREFER_CONFIRMED:
        provided: set[str] = set()
        if payload.title and payload.title.strip():
            provided.add("title")
        # BUG-276：按"是否显式提供"而非"是否非空"推断——空串/空列表是显式
        # 清空（契约 §5：清空字段不可被元数据补回），与未提供（None）不同。
        if payload.subtitle is not None:
            provided.add("subtitle")
        if payload.authors is not None or payload.author:
            provided.add("authors")
        if payload.isbn and payload.isbn.strip():
            provided.add("isbn")
        return provided
    return set()


def _field_conflict_warning(
    field: str,
    confirmed_value,
    metadata_value,
    source: str | None,
) -> IntakeWarning:
    return IntakeWarning(
        WARNING_METADATA_FIELD_CONFLICT,
        f"外部元数据{field}与确认值不一致，已保留确认值",
        field=field,
        detail={"confirmed": confirmed_value, "metadata": metadata_value, "source": source},
    )


def _same_author_set(a: list[str], b: list[str]) -> bool:
    return {x.strip().lower() for x in a} == {x.strip().lower() for x in b}


def _check_isbn_ownership(
    db: Session,
    *,
    isbn13: str | None,
    isbn10: str | None,
    title: str | None,
    original_title: str | None,
    original_authors: list[str] | None,
) -> None:
    """BI-03（契约 §5）：ISBN 归属冲突阻断。

    ISBN 键命中已有书且书名/作者与其明显不一致 → 抛 ValueError（400，
    错误码 isbn_ownership_conflict），不自动更新、不绑定，转人工核对。
    判别口径：
    - 证据仅取调用方显式提供的原始书名/作者（original_title/original_authors）；
      展示书名 title 与当前 authors 可能已被外部元数据改写或为合成占位
      （如纯 ISBN 输入时的 "ISBN {isbn}"），不得充当归属证据（BUG-271）；
    - 书名用已有书的展示书名与 normalized_title 双锚点（BUG-163/169 语义）；
    - 作者比较解析后作者与原始输入作者（忽略大小写/空白）；
    - 任一方吻合即视为同一书放行；无显式书名/作者证据（纯 ISBN 输入）
      无从判别，维持既有绑定行为。
    """
    lookup_keys: set[str] = set()
    if isbn13:
        lookup_keys |= isbn_lookup_keys(isbn13)
    if isbn10:
        lookup_keys |= isbn_lookup_keys(isbn10)
    if not lookup_keys:
        return
    existing = db.scalar(
        select(Book).where(or_(Book.isbn13.in_(lookup_keys), Book.isbn10.in_(lookup_keys)))
    )
    if not existing:
        return

    input_title_norms = (
        {normalize_title(original_title)} if original_title and original_title.strip() else set()
    )
    existing_title_norms = {
        normalize_title(t) for t in (existing.title, existing.normalized_title) if t and t.strip()
    }
    title_match = bool(input_title_norms & existing_title_norms)

    existing_authors = deserialize_json_list(existing.authors) or []
    existing_author_set = {a.strip().lower() for a in existing_authors}
    author_match = any(
        bool({a.strip().lower() for a in group} & existing_author_set)
        for group in (original_authors,)
        if group
    ) if existing_author_set else False

    if title_match or author_match:
        return
    if not input_title_norms and not original_authors:
        return  # 纯 ISBN 输入（含元数据派生/合成书名），无从判别归属，维持既有绑定
    display = (original_title or title or "").strip()
    raise ValueError(
        f"ISBN 归属冲突：{isbn13 or isbn10} 已绑定《{existing.title}》，"
        f"与当前输入《{display}》明显不一致，请人工核对"
    )


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
    warnings: list[IntakeWarning] | None = None,
    finalize: Callable[[IntakeResult], None] | None = None,
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

    message = f"《{existing.title}》已在书架中"
    if created_copy:
        message += "，已添加新副本"
    if created_purchase:
        message += "，已记录购买"
    if cover_backfilled:
        message += "，已补充封面"

    result = IntakeResult(
        action="exists",
        book=existing,
        matched_source=source if metadata else None,
        isbn_detected=isbn_detected,
        message=message,
        created_copy=created_copy,
        created_purchase=created_purchase,
        already_exists=True,
        warnings=warnings,
    )
    orphan_cover = existing.cover_path if cover_backfilled else None
    if created_copy or created_purchase or cover_backfilled or finalize is not None:
        try:
            db.flush()
            if finalize is not None:
                finalize(result)
            db.commit()
        except IntegrityError as exc:
            _cleanup_orphan_cover(orphan_cover)
            raise rollback_on_integrity(db, exc) from exc
        except Exception:
            db.rollback()
            _cleanup_orphan_cover(orphan_cover)
            raise
    return result


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
        # BUG-169：normalized_title 存的是原始输入书名（BUG-163），以展示书名
        # （元数据改写的英文译名，或用户/Agent 从书目详情读到的 title）再入库时
        # 索引不命中。回退按展示书名逐行比对——家庭藏书量级（数百本）O(N) 可接受。
        candidates = [
            book for book in db.scalars(select(Book)).all()
            if normalize_title(book.title or "") == normalized
        ]
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


def _find_existing_dedup(
    db: Session,
    *,
    isbn13: str | None,
    isbn10: str | None,
    title: str,
    authors: list[str] | None,
    original_title: str | None = None,
    original_authors: list[str] | None = None,
) -> Book | None:
    """BUG-163：去重查找，同时尝试元数据书名和原始输入书名。

    无 ISBN 入库时，元数据可能改写书名和作者（如 OpenLibrary 返回英文译名），
    导致同一输入因元数据命中/超时差异而绕过去重。新建时 normalized_title 与
    authors 均存原始输入（BUG-171），因此这里做两次严格匹配：
    元数据书名+当前作者 → 原始输入书名+原始作者。

    不做"不限作者"的宽松回退（BUG-171）：同书名不同作者的两本书会在候选
    唯一时被误合并。输入本身无作者时 _find_existing 内部保留"候选唯一即
    命中"的既有语义（无作者信号可判别，与 HEAD 行为一致）。
    """
    existing = _find_existing(db, isbn13=isbn13, isbn10=isbn10, title=title, authors=authors)
    if existing:
        return existing
    if not original_title:
        return None
    # 严格匹配原始输入书名 + 原始输入作者（覆盖元数据改写书名/作者的组合）
    return _find_existing(
        db, isbn13=isbn13, isbn10=isbn10, title=original_title, authors=original_authors
    )


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
