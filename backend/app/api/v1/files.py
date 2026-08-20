"""静态文件服务--为 Web UI 提供封面与附件的 HTTP 访问。

BUG-167：接入 files:read 鉴权（授权矩阵声明），匿名不再可读；
路径穿越防护确保只能访问 covers/attachments 目录内文件。
附件类文件强制 Content-Disposition: attachment，阻止 HTML/主动 SVG 等内联执行（BUG-116）。
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth_context import AuthContext, require_scope
from app.config import settings

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


@router.get("/attachments/{file_path:path}")
async def serve_attachment(
    file_path: str,
    _ctx: AuthContext = Depends(require_scope("files:read")),
) -> FileResponse:
    full = _safe_resolve(settings.attachments_dir, file_path)
    # BUG-116：HTML/主动 SVG 等危险类型强制下载，防止同源内联执行存储型主动内容
    # 同时覆盖 media_type 防止浏览器按 Content-Type 猜测渲染
    if _should_force_download(full):
        return FileResponse(full, filename=full.name, content_disposition_type="attachment", media_type=_DANGEROUS_MIME)
    return FileResponse(full)
