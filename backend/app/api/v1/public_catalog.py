"""Public Catalog API（权限阶段 1：C 模式匿名书架，基线 §3.2/§14-阶段1）。

独立只读接口：匿名局域网用户浏览 L1 共享书目，与完整业务 API（/api/v1/books*，
始终要求认证）分离，避免"为了匿名首页而放开完整业务 API"。

每请求三重门控（顺序固定）：
1. 模式门控：anonymous_catalog_mode 必须为 lan_shared（explicit_public 属
   阶段 4 B 模式，本期按关闭处理）；
2. 信任门控：来源必须可验证位于可信网络（回环 / TRUSTED_LAN_CIDRS /
   可信代理右值法还原），否则自动降级（403 LAN_REQUIRED，前端显示登录入口）；
3. 限流门控：每客户端 IP 固定窗口计数（共享 rate_limit 服务），超限 429。

输出只有 Catalog Read Model 的 L1 白名单字段；错误使用稳定机器码
（ANONYMOUS_CATALOG_DISABLED / LAN_REQUIRED / RATE_LIMITED / BOOK_NOT_FOUND /
COVER_NOT_FOUND，基线 §9.4）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Book
from app.schemas.book import ApiResponse
from app.services import catalog_read, rate_limit, security_audit, trusted_network

router = APIRouter(prefix="/public-catalog", tags=["public-catalog"])

# 封面只允许图片后缀（不提供 HTML/SVG 等可执行内容，BUG-116 同思路）
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})


def _denied(status_code: int, code: str, retry_after: int | None = None) -> JSONResponse:
    payload = ApiResponse(ok=False, data=None, error=code).model_dump()
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def _audit(
    request: Request, outcome: str, reason: str, decision_ip: str | None
) -> None:
    """安全审计（CHK-071 遗漏项 5）：拒绝必记、放行采样，写入失败不影响响应。"""
    security_audit.log_security_event(
        event_type="public_catalog.access",
        outcome=outcome,
        subject=decision_ip,
        ip=request.client.host if request.client else None,
        details={"reason": reason},
    )


def _gate(request: Request, *, bucket: str) -> JSONResponse | None:
    """模式 + 可信网络 + 限流三重门控；全部通过返回 None，否则返回拒绝响应。

    CHK-071：所有门控结果进入共享安全审计（拒绝/超限必记并按主体抑制聚合，
    放行采样留痕），写入失败不阻断业务。
    """
    if settings.anonymous_catalog_mode != "lan_shared":
        _audit(request, "deny", "ANONYMOUS_CATALOG_DISABLED", None)
        return _denied(403, "ANONYMOUS_CATALOG_DISABLED")
    decision = trusted_network.evaluate_request_trust(request)
    if not decision.trusted:
        _audit(request, "deny", "LAN_REQUIRED", decision.client_ip)
        return _denied(403, "LAN_REQUIRED")
    rl = rate_limit.check(
        f"{bucket}:{decision.client_ip or 'unknown'}",
        limit=settings.public_catalog_rate_limit_per_minute,
        window_seconds=60,
    )
    if not rl.allowed:
        _audit(request, "deny", "RATE_LIMITED", decision.client_ip)
        return _denied(429, "RATE_LIMITED", retry_after=rl.retry_after_seconds)
    _audit(request, "allow", "ok", decision.client_ip)
    return None


@router.get("/books")
def list_public_books(
    request: Request,
    query: str | None = Query(default=None, max_length=200),
    author: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=100),
    language: str | None = Query(default=None, max_length=20),
    availability: str | None = Query(default=None, pattern="^(in_shelf|borrowed|unknown)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    db: Session = Depends(get_db),
):
    denied = _gate(request, bucket="public-catalog:books")
    if denied is not None:
        return denied
    page_size = min(page_size, settings.public_catalog_max_page_size)
    result = catalog_read.search_catalog(
        db,
        query=query, author=author, category=category,
        language=language, availability=availability,
        page=page, page_size=page_size,
    )
    # CHK-071：可访问性取决于来源 IP，禁止共享缓存（public 会让共享反代把
    # LAN 用户取得的响应直接给公网用户，绕过信任门控）——仅允许私有缓存
    return JSONResponse(
        status_code=200,
        content=ApiResponse(ok=True, data=result.model_dump(), error=None).model_dump(),
        headers={"Cache-Control": "private, max-age=120"},
    )


@router.get("/books/{book_id}")
def get_public_book(
    book_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    denied = _gate(request, bucket="public-catalog:books")
    if denied is not None:
        return denied
    detail = catalog_read.get_catalog_book(db, book_id)
    if detail is None:
        # 不区分"不存在"与"不可见"，防枚举（基线 §9.4）
        return _denied(404, "BOOK_NOT_FOUND")
    return ApiResponse(ok=True, data=detail.model_dump(), error=None)


def _resolve_cover(cover_path: str) -> Path | None:
    """cover_path 形如 covers/xxx.jpg → covers 目录内文件（防路径穿越）。"""
    filename = Path(cover_path).name  # 只取文件名，丢弃任何目录成分
    target = (settings.covers_dir / filename).resolve()
    if not target.is_relative_to(settings.covers_dir.resolve()):
        return None
    if not target.is_file() or target.suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    return target


def _ensure_thumbnail(src: Path) -> Path:
    """生成缓存缩略图（PIL 可用时）；PIL 缺失或解码失败回退原图。

    BUG-182：缓存命中检查与落盘路径必须是同一文件（统一 .jpg 落盘）。
    BUG-194：缓存键包含源扩展名（同名不同扩展不互相覆盖）；源文件比缩略图
    新时重新生成（封面被替换后不再返回陈旧缩略图）。
    """
    dst = settings.covers_dir / ".thumbs" / f"{src.stem}_{src.suffix.lstrip('.')}.jpg"
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    try:
        from PIL import Image

        with Image.open(src) as im:
            im.thumbnail((400, 600))
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, format="JPEG", quality=80)
        return dst
    except Exception:
        return src


@router.get("/covers/{book_id}")
def get_public_cover(
    book_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    denied = _gate(request, bucket="public-catalog:covers")
    if denied is not None:
        return denied
    book = db.get(Book, book_id)
    if book is None or not book.cover_path:
        return _denied(404, "COVER_NOT_FOUND")
    src = _resolve_cover(book.cover_path)
    if src is None:
        return _denied(404, "COVER_NOT_FOUND")
    thumb = _ensure_thumbnail(src)
    # CHK-071：同上，IP 门控资源不用 public 缓存
    return FileResponse(thumb, headers={"Cache-Control": "private, max-age=86400"})
