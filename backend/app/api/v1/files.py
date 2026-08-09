"""静态文件服务--为 Web UI 提供封面与附件的 HTTP 访问。

一期保持局域网信任模型，不做鉴权；路径穿越防护确保只能访问 covers/attachments 目录内文件。
附件类文件强制 Content-Disposition: attachment，阻止 HTML/主动 SVG 等内联执行（BUG-116）。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(prefix="/files", tags=["files"])

# 可能包含可执行脚本/主动内容的文件后缀，必须以附件方式下载而非内联展示
# BUG-116：含 .shtml/.xht/.svgz 等 HTML/SVG 变体，防止同源内联执行
_FORCE_DOWNLOAD_SUFFIXES = frozenset({
    ".html", ".htm", ".shtml", ".xhtml", ".xht", ".svg", ".svgz", ".xml",
    ".js", ".mjs", ".css",
    ".pdf",  # PDF 内嵌 JS 风险
    ".swf",
})


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
async def serve_cover(file_path: str) -> FileResponse:
    full = _safe_resolve(settings.covers_dir, file_path)
    # BUG-116：封面也强制危险类型下载，防止 .svg/.html 等同源内联执行
    if _should_force_download(full):
        return FileResponse(full, filename=full.name, content_disposition_type="attachment")
    return FileResponse(full)


@router.get("/attachments/{file_path:path}")
async def serve_attachment(file_path: str) -> FileResponse:
    full = _safe_resolve(settings.attachments_dir, file_path)
    # BUG-116：HTML/主动 SVG 等危险类型强制下载，防止同源内联执行存储型主动内容
    if _should_force_download(full):
        return FileResponse(full, filename=full.name, content_disposition_type="attachment")
    return FileResponse(full)
