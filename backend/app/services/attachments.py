from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Attachment, Book, BookCopy, Member, ReadingNote
from app.schemas.attachment import AttachmentCreate
from app.utils.book_helpers import sanitize_filename_stem
from app.utils.db_errors import rollback_on_integrity


ALLOWED_ENTITY_TYPES = frozenset({"book", "member", "note", "copy"})

# BUG-116：禁止上传可内联执行脚本/主动内容的文件类型，防止存储型 XSS
# 含 .shtml/.xht/.svgz 等服务端解析或等价 HTML/SVG 变体
_BLOCKED_EXTENSIONS = frozenset({
    ".html", ".htm", ".shtml", ".xhtml", ".xht", ".svg", ".svgz", ".xml",
    ".js", ".mjs", ".css",
    ".swf",
})


@dataclass
class AttachmentResult:
    attachment: Attachment
    message: str


def _validate_entity(db: Session, entity_type: str, entity_id: int) -> None:
    if not isinstance(entity_type, str) or entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValueError(f"不支持的实体类型: {entity_type!r}")
    if not isinstance(entity_id, int) or entity_id <= 0:
        raise ValueError("entity_id 必须为正整数")
    if entity_type == "book" and not db.get(Book, entity_id):
        raise ValueError(f"书籍 ID {entity_id} 不存在")
    if entity_type == "member" and not db.get(Member, entity_id):
        raise ValueError(f"成员 ID {entity_id} 不存在")
    if entity_type == "note" and not db.get(ReadingNote, entity_id):
        raise ValueError(f"笔记 ID {entity_id} 不存在")
    if entity_type == "copy" and not db.get(BookCopy, entity_id):
        raise ValueError(f"副本 ID {entity_id} 不存在")


def create_attachment(
    db: Session,
    payload: AttachmentCreate,
    *,
    upload_path: Path | None = None,
) -> AttachmentResult:
    _validate_entity(db, payload.entity_type, payload.entity_id)

    file_path: str | None = None
    dest: Path | None = None
    if upload_path:
        settings.attachments_dir.mkdir(parents=True, exist_ok=True)
        suffix = upload_path.suffix or ".bin"
        # BUG-116：拒绝危险扩展名，阻止存储型 XSS
        if suffix.lower() in _BLOCKED_EXTENSIONS:
            raise ValueError(f"不允许上传此类型文件: {suffix}")
        entity_part = sanitize_filename_stem(payload.entity_type)
        title_part = sanitize_filename_stem(payload.title or "file")
        # 追加短 uuid 避免同实体同标题覆盖先前附件
        unique_tag = uuid.uuid4().hex[:8]
        candidate = settings.attachments_dir / f"{entity_part}_{payload.entity_id}_{title_part}_{unique_tag}{suffix}"
        try:
            candidate.resolve().relative_to(settings.attachments_dir.resolve())
        except ValueError as exc:
            raise ValueError("非法的附件路径") from exc
        dest = candidate
        shutil.copy2(upload_path, dest)
        file_path = str(dest.relative_to(settings.data_dir))

    if payload.attach_type == "link" and not payload.url and not file_path:
        raise ValueError("链接类型附件需提供 url 或上传文件")
    if payload.attach_type == "file" and not file_path:
        raise ValueError("文件类型附件需上传文件")
    if payload.attach_type == "markdown" and not payload.content_md and not file_path:
        raise ValueError("markdown 类型附件需提供 content_md 或上传文件")

    attachment = Attachment(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        attach_type=payload.attach_type,
        title=payload.title,
        url=payload.url,
        file_path=file_path,
        content_md=payload.content_md,
        mime_type=payload.mime_type,
        sort_order=payload.sort_order,
    )
    db.add(attachment)
    try:
        db.commit()
    except IntegrityError as exc:
        if dest and dest.exists():
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(attachment)
    return AttachmentResult(attachment=attachment, message="附件已保存")