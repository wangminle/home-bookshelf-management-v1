from __future__ import annotations

from fastapi import HTTPException, UploadFile

# 与 storage.MAX_COVER_BYTES 对齐
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
_CHUNK = 64 * 1024


async def read_upload_limited(
    upload: UploadFile,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> bytes:
    """流式读取上传文件，超限立即 413，避免全量读入内存。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"上传文件超过大小上限（{max_bytes // (1024 * 1024)}MB）",
            )
        chunks.append(chunk)
    return b"".join(chunks)
