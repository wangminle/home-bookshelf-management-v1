from fastapi import APIRouter, Response, status

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import SessionLocal
from app.schemas.agent_discovery import PublicHealthData
from app.schemas.book import ApiResponse, HealthOut
from app.services.agent_discovery import build_public_health

router = APIRouter()


def _barcode_scan_available() -> bool:
    try:
        from PIL import Image  # noqa: F401
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError:
        return False
    return True


@router.get("/public-health", response_model=ApiResponse)
def public_health() -> ApiResponse:
    """WBS-2：公共健康检查。只返回最小可用性，不暴露数据库状态、第三方 Key 配置等内部细节。"""
    return ApiResponse(ok=True, data=build_public_health(), error=None)


@router.get("/health", response_model=ApiResponse)
def health_check(response: Response) -> ApiResponse:
    """WBS-2：受保护诊断端点。暴露数据库连接、Google Books Key 配置和条码依赖状态。
    WBS-6 完成后此端点需要 owner 会话或 agent Token 授权。"""
    database = "connected"
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        database = "disconnected"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    ok = database == "connected"
    return ApiResponse(
        ok=ok,
        data=HealthOut(
            status="ok" if ok else "degraded",
            app=settings.app_name,
            database=database,
            google_books_configured=bool(settings.google_books_api_key),
            barcode_scan_available=_barcode_scan_available(),
        ),
        error=None if ok else "database disconnected",
    )
