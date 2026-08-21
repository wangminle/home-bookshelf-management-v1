import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth_context import AuthContext, require_scope, verify_csrf
from app.config import settings
from app.db import get_db
from app.models import Book, ReadingProgress
from app.schemas.book import ApiResponse, BookCreate, BookListOut, BookUpdate
from app.services.books import delete_book, get_book_detail, merge_books, set_book_cover, update_book
from app.services.storage import save_uploaded_image
from app.utils.book_helpers import (
    canonical_isbn13,
    escape_like,
    isbn_lookup_keys,
    like_pattern,
    normalize_isbn,
    normalize_title,
    sanitize_filename_stem,
    serialize_json_list,
)
from app.utils.db_errors import ConflictError, rollback_on_integrity
from app.utils.operation_log import log_and_commit
from app.utils.serializers import book_to_out
from app.utils.uploads import read_upload_limited

router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=ApiResponse)
def list_books(
    keyword: str | None = Query(default=None),
    author: str | None = Query(default=None),
    isbn: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None, description="阅读状态过滤"),
    member_id: int | None = Query(default=None, description="配合 status 过滤指定成员"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("books:read")),
) -> ApiResponse:
    stmt = select(Book)
    count_stmt = select(func.count()).select_from(Book)
    conditions = []

    if keyword:
        pattern = like_pattern(keyword)
        conditions.append(
            or_(
                Book.title.ilike(pattern, escape="\\"),
                Book.normalized_title.ilike(pattern, escape="\\"),
            )
        )
    if author:
        # authors 以 json.dumps 落库；对检索词做 JSON 字符串转义后再拼 LIKE，避免 " \ 漏配
        import json as _json

        author_json = _json.dumps(author.strip(), ensure_ascii=False)[1:-1]  # 去掉首尾引号
        author_pattern = f"%{escape_like(author_json)}%"
        conditions.append(Book.authors.ilike(author_pattern, escape="\\"))
    if isbn:
        keys = isbn_lookup_keys(normalize_isbn(isbn) or isbn.strip())
        if keys:
            conditions.append(or_(Book.isbn13.in_(keys), Book.isbn10.in_(keys)))
        else:
            pattern = like_pattern(isbn)
            conditions.append(
                or_(
                    Book.isbn13.ilike(pattern, escape="\\"),
                    Book.isbn10.ilike(pattern, escape="\\"),
                )
            )
    if category:
        pattern = like_pattern(category)
        conditions.append(Book.category.ilike(pattern, escape="\\"))

    if member_id is not None and not status:
        raise HTTPException(status_code=400, detail="member_id 必须配合 status 参数一起使用")
    if status:
        # BUG-117/123：状态筛选口径与 GET /stats 完全一致——每本书聚合成单一全局状态。
        # 无 member_id 时按全部成员聚合；带 member_id 时仅按该成员的进度聚合。
        from app.utils.book_helpers import aggregate_book_status

        prog_stmt = select(ReadingProgress.book_id, ReadingProgress.status)
        if member_id is not None:
            prog_stmt = prog_stmt.where(ReadingProgress.member_id == member_id)
        progress_rows = db.execute(prog_stmt).all()
        book_member_statuses: dict[int, list[str]] = {}
        for bid, st in progress_rows:
            book_member_statuses.setdefault(bid, []).append(st)
        # 全局状态 == status 的书
        matched_book_ids = {
            bid
            for bid, statuses in book_member_statuses.items()
            if aggregate_book_status(statuses) == status
        }
        # unread 还要包含完全无进度记录的书（与 stats 口径一致）
        if status == "unread":
            books_with_progress_ids = set(book_member_statuses.keys())
            all_book_ids = set(db.scalars(select(Book.id)).all())
            matched_book_ids |= all_book_ids - books_with_progress_ids
        conditions.append(Book.id.in_(matched_book_ids) if matched_book_ids else Book.id == -1)

    if conditions:
        combined = conditions[0] if len(conditions) == 1 else and_(*conditions)
        stmt = stmt.where(combined)
        count_stmt = count_stmt.where(combined)

    total = db.scalar(count_stmt) or 0
    books = db.scalars(stmt.order_by(Book.updated_at.desc()).offset(offset).limit(limit)).all()
    return ApiResponse(data=BookListOut(items=[book_to_out(b) for b in books], total=total))


@router.post("", response_model=ApiResponse, status_code=201)
def create_book(
    payload: BookCreate,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("books:write")),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    isbn13 = canonical_isbn13(payload.isbn13) or canonical_isbn13(payload.isbn10)
    isbn10 = normalize_isbn(payload.isbn10)
    lookup_keys = isbn_lookup_keys(isbn13) | isbn_lookup_keys(isbn10)
    if lookup_keys:
        existing = db.scalar(select(Book).where(or_(Book.isbn13.in_(lookup_keys), Book.isbn10.in_(lookup_keys))))
        if existing:
            raise HTTPException(status_code=409, detail="书籍已存在（ISBN 冲突）")

    book = Book(
        title=payload.title.strip(),
        subtitle=payload.subtitle,
        isbn13=isbn13,
        isbn10=isbn10,
        normalized_title=normalize_title(payload.title),
        authors=serialize_json_list(payload.authors),
        publisher=payload.publisher,
        publish_date=payload.publish_date,
        page_count=payload.page_count,
        language=payload.language,
        category=payload.category,
        summary=payload.summary,
        source="manual",
    )
    db.add(book)
    try:
        db.commit()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(rollback_on_integrity(db, exc))) from exc
    db.refresh(book)
    log_and_commit(db, action="book.create", payload={"book_id": book.id, "isbn13": book.isbn13, "title": book.title})
    return ApiResponse(data=book_to_out(book))


@router.get("/{book_id}", response_model=ApiResponse)
def get_book(
    book_id: int,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_scope("books:read")),
) -> ApiResponse:
    """BUG-166：详情接 books:read 鉴权；敏感子资源按各自 scope 过滤。

    BUG-192：进度/日志/笔记/购买属 L3 成员私有——非 Web Owner 主体只能
    看到自己 member_id 的记录（Web Owner 保持全量+代操作口径）。
    """
    try:
        data = get_book_detail(db, book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # books:read 不隐含读进度/购买/笔记：缺对应 scope 的调用方不下发这些子资源
    _owner_view = ctx.auth_type == "web" and ctx.is_owner
    if "reading:read" in ctx.scopes:
        if not _owner_view and ctx.member_id is not None:
            data["reading_progress"] = [
                p for p in data.get("reading_progress", []) if p.get("member_id") == ctx.member_id
            ]
    else:
        data.pop("reading_progress", None)
    if "purchases:read" in ctx.scopes:
        if not _owner_view and ctx.member_id is not None:
            data["purchase_records"] = [
                p for p in data.get("purchase_records", []) if p.get("buyer_member_id") == ctx.member_id
            ]
    else:
        data.pop("purchase_records", None)
    if "notes:read" in ctx.scopes:
        if not _owner_view and ctx.member_id is not None:
            data["reading_notes"] = [
                n for n in data.get("reading_notes", []) if n.get("member_id") == ctx.member_id
            ]
    else:
        data.pop("reading_notes", None)
    return ApiResponse(data=data)


@router.patch("/{book_id}", response_model=ApiResponse)
def patch_book(
    book_id: int,
    payload: BookUpdate,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("books:write")),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    try:
        result = update_book(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(db, action="book.update", payload={"book_id": book_id})
    return ApiResponse(data={**book_to_out(result.book).model_dump(), "message": result.message})


@router.delete("/{book_id}", response_model=ApiResponse)
def remove_book(
    book_id: int,
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("books:delete")),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """BUG-147：删除一本书。此前全 API 无 DELETE 端点，录错只能降级到直接操作 SQLite。"""
    try:
        title = delete_book(db, book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(db, action="book.delete", payload={"book_id": book_id, "title": title})
    return ApiResponse(data={"id": book_id, "message": f"已删除《{title}》"})


@router.post("/{book_id}/merge", response_model=ApiResponse)
def merge_book(
    book_id: int,
    source_id: int = Query(..., description="被合并进来的源书 ID（合并后删除）"),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("books:delete")),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """BUG-148：把 source 书合并进 target（book_id），迁移副本/购买/进度/笔记/附件/标签后删 source。

    权限阶段 0：合并会删除源书记录（破坏性），按授权矩阵契约要求
    books:delete——此前误用 books:write，与矩阵声明不一致。"""
    try:
        result = merge_books(db, target_id=book_id, source_id=source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(
        db,
        action="book.merge",
        payload={"target_id": book_id, "source_id": source_id, "source_title": result.source_title},
    )
    return ApiResponse(
        data={
            **book_to_out(result.target).model_dump(),
            "source_title": result.source_title,
            "migrated": {
                "copies": result.migrated_copies,
                "purchases": result.migrated_purchases,
                "progress": result.migrated_progress,
                "notes": result.migrated_notes,
                "logs": result.migrated_logs,
                "attachments": result.migrated_attachments,
                "custom_fields": result.migrated_custom_fields,
                "tags": result.merged_tags,
            },
            "message": f"已将《{result.source_title}》合并入《{result.target.title}》",
        }
    )


@router.post("/{book_id}/cover", response_model=ApiResponse)
async def set_cover(
    book_id: int,
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("books:write")),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """BUG-151：上传图片设为指定书的封面。

    POST /books 不处理封面，cover_path 与附件表无联动；
    本端点提供统一的"设封面"入口，落盘后写入 books.cover_path。
    """
    if not image.filename:
        raise HTTPException(status_code=400, detail="请上传图片文件")

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail=f"书籍 ID {book_id} 不存在")

    suffix = Path(image.filename).suffix or ".jpg"
    temp_file: Path | None = None
    try:
        content = await read_upload_limited(image)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_file = Path(tmp.name)
            tmp.write(content)

        # 落盘到 covers/，文件名优先 ISBN，其次书名归一化。
        # 必须追加 book_id 后缀：无 ISBN 且同名的两本书会生成相同目标文件名，
        # overwrite=True 会直接覆盖另一本书正在引用的封面。加 book_id 后保证唯一。
        name_base = canonical_isbn13(book.isbn13) or book.isbn10 or normalize_title(book.title) or "book"
        target_name = f"{name_base}_{book.id}"
        # save_uploaded_image 是同步 IO，放线程池避免阻塞事件循环（与 intake/recognize 一致）
        cover_rel = await run_in_threadpool(
            save_uploaded_image, temp_file, target_name, overwrite=True
        )
    except HTTPException:
        raise
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=f"保存封面失败：{exc}") from exc
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    if not cover_rel:
        raise HTTPException(status_code=500, detail="封面落盘失败")

    try:
        book = set_book_cover(db, book_id, cover_rel)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(db, action="book.set_cover", payload={"book_id": book_id, "cover_path": cover_rel})
    return ApiResponse(data={**book_to_out(book).model_dump(), "message": "封面已设置"})
