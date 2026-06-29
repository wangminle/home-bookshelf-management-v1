import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.intake import IntakeOut, IntakeRequest
from app.services.intake import IntakeInput, IntakeResult, intake_book
from app.utils.db_errors import ConflictError
from app.utils.serializers import book_to_out

router = APIRouter(prefix="/books", tags=["books"])


def _build_intake_response(result: IntakeResult) -> tuple[IntakeOut, int]:
    data = IntakeOut(
        action=result.action,
        book=book_to_out(result.book),
        matched_source=result.matched_source,
        isbn_detected=result.isbn_detected,
        message=result.message,
        created_copy=result.created_copy,
        created_purchase=result.created_purchase,
        already_exists=result.already_exists,
    )
    status_code = 200 if result.already_exists else 201
    return data, status_code


@router.post("/intake", response_model=ApiResponse)
async def intake(
    response: Response,
    isbn: str | None = Form(default=None),
    title: str | None = Form(default=None),
    author: str | None = Form(default=None),
    price: float | None = Form(default=None),
    channel: str | None = Form(default=None),
    location: str | None = Form(default=None),
    member_id: int | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse:
    image_path: Path | None = None
    temp_file: Path | None = None

    try:
        if image and image.filename:
            suffix = Path(image.filename).suffix or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_file = Path(tmp.name)
                content = await image.read()
                tmp.write(content)
            image_path = temp_file

        result = await run_in_threadpool(
            intake_book,
            db,
            IntakeInput(
                isbn=isbn,
                title=title,
                author=author,
                image_path=image_path,
                price=price,
                channel=channel,
                location=location,
                member_id=member_id,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    data, status_code = _build_intake_response(result)
    response.status_code = status_code
    return ApiResponse(data=data.model_dump())


@router.post("/intake/json", response_model=ApiResponse)
def intake_json(payload: IntakeRequest, response: Response, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        result = intake_book(
            db,
            IntakeInput(
                isbn=payload.isbn,
                title=payload.title,
                author=payload.author,
                price=payload.price,
                channel=payload.channel,
                location=payload.location,
                member_id=payload.member_id,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    data, status_code = _build_intake_response(result)
    response.status_code = status_code
    return ApiResponse(data=data.model_dump())
