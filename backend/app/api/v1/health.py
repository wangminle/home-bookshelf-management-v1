from fastapi import APIRouter, Response, status

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import SessionLocal
from app.schemas.book import ApiResponse, HealthOut

router = APIRouter()


def _barcode_scan_available() -> bool:
    try:
        from PIL import Image  # noqa: F401
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError:
        return False
    return True


@router.get("/health", response_model=ApiResponse)
def health_check(response: Response) -> ApiResponse:
    database = "connected"
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        database = "disconnected"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ApiResponse(
        data=HealthOut(
            app=settings.app_name,
            database=database,
            google_books_configured=bool(settings.google_books_api_key),
            barcode_scan_available=_barcode_scan_available(),
        )
    )
