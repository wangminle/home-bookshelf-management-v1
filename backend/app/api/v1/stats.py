from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.services.stats import get_stats

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=ApiResponse)
def stats(db: Session = Depends(get_db)) -> ApiResponse:
    return ApiResponse(data=get_stats(db).model_dump())
