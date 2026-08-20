from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_scope, resolve_body_member, verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.purchase import PurchaseCreate, PurchaseOut
from app.services.purchase import create_purchase
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit

router = APIRouter(prefix="/books", tags=["purchases"])


@router.post("/{book_id}/purchases", response_model=ApiResponse, status_code=201)
def add_purchase(
    book_id: int,
    payload: PurchaseCreate,
    ctx: AuthContext = Depends(require_scope("purchases:write")),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    member_id = resolve_body_member(ctx, payload.member_id, db=db)
    payload = payload.model_copy(update={"member_id": member_id})
    try:
        result = create_purchase(db, book_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_and_commit(
        db,
        action="purchase.create",
        member_id=result.purchase.buyer_member_id,
        channel=ctx.channel,
        payload={"book_id": book_id, "purchase_id": result.purchase.id},
    )

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