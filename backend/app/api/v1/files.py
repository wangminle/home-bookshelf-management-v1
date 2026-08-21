"""静态文件服务--为 Web UI 提供封面与附件的 HTTP 访问。

BUG-167：接入 files:read 鉴权（授权矩阵声明），匿名不再可读；
路径穿越防护确保只能访问 covers/attachments 目录内文件。
附件类文件强制 Content-Disposition: attachment，阻止 HTML/主动 SVG 等内联执行（BUG-116）。
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope
from app.config import settings
from app.db import get_db
from app.models import Attachment, Member, ReadingNote

router = APIRouter(prefix="/files", tags=["files"])

# 可能包含可执行脚本/主动内容的文件后缀，必须以附件方式下载而非内联展示
# BUG-116：含 .shtml/.xht/.svgz/.shtm 等 HTML/SVG 变体，防止同源内联执行
_FORCE_DOWNLOAD_SUFFIXES = frozenset({
    ".html", ".htm", ".shtml", ".shtm", ".xhtml", ".xht", ".svg", ".svgz", ".xml",
    ".js", ".mjs", ".css",
    ".pdf",  # PDF 内嵌 JS 风险
    ".swf",
})

# BUG-116：危险类型强制 application/octet-stream，防止浏览器按 Content-Type
# 猜测（如 text/html）在下载栏中内联渲染
_DANGEROUS_MIME = "application/octet-stream"


def _safe_resolve(base_dir, file_path: str):
    """解析路径并确保不会逃出 base_dir。返回绝对路径或抛 404。"""
    target = (base_dir / file_path).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


def _should_force_download(path) -> bool:
    return path.suffix.lower() in _FORCE_DOWNLOAD_SUFFIXES


@router.get("/covers/{file_path:path}")
async def serve_cover(
    file_path: str,
    _ctx: AuthContext = Depends(require_scope("files:read")),
) -> FileResponse:
    full = _safe_resolve(settings.covers_dir, file_path)
    # BUG-116：封面也强制危险类型下载，防止 .svg/.html 等同源内联执行
    # 同时覆盖 media_type 防止浏览器按 Content-Type 猜测渲染
    if _should_force_download(full):
        return FileResponse(full, filename=full.name, content_disposition_type="attachment", media_type=_DANGEROUS_MIME)
    return FileResponse(full)


def _attachment_owner_member_id(db: Session, attachment: Attachment) -> int | None:
    """附件的私有归属成员：member 实体=本人；note 实体=笔记作者；book/copy=家庭共享(None)。"""
    if attachment.entity_type == "member":
        return attachment.entity_id
    if attachment.entity_type == "note":
        note = db.get(ReadingNote, attachment.entity_id)
        return note.member_id if note else None
    return None


@router.get("/attachments/{file_path:path}")
async def serve_attachment(
    file_path: str,
    ctx: AuthContext = Depends(require_scope("files:read")),
    db: Session = Depends(get_db),
) -> FileResponse:
    """附件下载（权限阶段 2）：继承父资源权限，不能只凭附件路径。

    - 挂在 member/note 实体上的附件属 L3 私有：仅 Web Owner 或归属成员本人；
    - book/copy 附件与无归属记录（历史孤儿文件）：files:read 即可（家庭共享口径）。
    """
    full = _safe_resolve(settings.attachments_dir, file_path)
    # BUG-202：归属查询必须基于归一化路径。原始 file_path 可携带 ./、/../、/.
    # 等片段（raw HTTP 不做路径规范化，_safe_resolve 会解析到同一文件），
    # 按原始串查库会 miss 附件记录，落入"无归属=家庭共享"分支，绕过 L3 门禁。
    rel = full.relative_to(settings.attachments_dir.resolve()).as_posix()
    attachment = db.scalar(
        select(Attachment).where(Attachment.file_path == f"attachments/{rel}")
    )
    if attachment is not None:
        owner_member_id = _attachment_owner_member_id(db, attachment)
        if owner_member_id is not None:
            is_owner_view = ctx.auth_type == "web" and ctx.is_owner
            if not is_owner_view and ctx.member_id != owner_member_id:
                # 404 防枚举：不暴露该附件是否存在
                raise HTTPException(status_code=404, detail="文件不存在")
    # BUG-116：HTML/主动 SVG 等危险类型强制下载，防止同源内联执行存储型主动内容
    # 同时覆盖 media_type 防止浏览器按 Content-Type 猜测渲染
    if _should_force_download(full):
        return FileResponse(full, filename=full.name, content_disposition_type="attachment", media_type=_DANGEROUS_MIME)
    return FileResponse(full)
