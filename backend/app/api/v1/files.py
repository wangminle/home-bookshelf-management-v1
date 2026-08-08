"""静态文件服务——为 Web UI 提供封面与附件的 HTTP 访问。

一期保持局域网信任模型，不做鉴权；路径穿越防护确保只能访问 covers/attachments 目录内文件。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(prefix="/files", tags=["files"])


def _safe_resolve(base_dir, file_path: str):
    """解析路径并确保不会逃出 base_dir。返回绝对路径或抛 404。"""
    target = (base_dir / file_path).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


@router.get("/covers/{file_path:path}")
async def serve_cover(file_path: str) -> FileResponse:
    full = _safe_resolve(settings.covers_dir, file_path)
    return FileResponse(full)


@router.get("/attachments/{file_path:path}")
async def serve_attachment(file_path: str) -> FileResponse:
    full = _safe_resolve(settings.attachments_dir, file_path)
    return FileResponse(full)
