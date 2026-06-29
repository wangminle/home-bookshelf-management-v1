from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.purchase import PurchaseCreate, PurchaseOut
from app.services.purchase import create_purchase
from app.utils.db_errors import ConflictError

router = APIRouter(prefix="/books", tags=["purchases"])


@router.post("/{book_id}/purchases", response_model=ApiResponse, status_code=201)
def add_purchase(
    book_id: int,
    payload: PurchaseCreate,
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        result = create_purchase(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    data = PurchaseOut(
        id=result.purchase.id,
        book_id=result.purchase.book_id,
        price=result.purchase.price or payload.price,
        original_price=result.purchase.original_price,
        channel=result.purchase.channel,
        order_no=result.purchase.order_no,
        purchase_date=result.purchase.purchase_date,
        currency=result.purchase.currency,
        buyer_member_id=result.purchase.buyer_member_id,
        created_at=result.purchase.created_at,
        message=result.message,
    )
    return ApiResponse(data=data.model_dump())
