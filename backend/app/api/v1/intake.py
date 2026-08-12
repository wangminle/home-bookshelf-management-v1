import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import db as db_module
from app.auth import ChannelIdentity, channel_headers, enforce_channel_member
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.intake import IntakeOut, IntakeRequest
from app.services.intake import IntakeInput, IntakeResult, intake_book
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit
from app.utils.serializers import book_to_out
from app.utils.uploads import read_upload_limited

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


def _run_intake_in_thread(
    *,
    identity: ChannelIdentity,
    isbn: str | None,
    title: str | None,
    author: str | None,
    image_path: Path | None,
    price: float | None,
    channel: str | None,
    location: str | None,
    member_id: int | None,
) -> tuple[dict, int]:
    with db_module.SessionLocal() as db:
        resolved_member_id = enforce_channel_member(
            db,
            body_member_id=member_id,
            channel=identity.channel,
            external_user_id=identity.external_user_id,
            authorization=identity.authorization,
            web_session_token=identity.web_session_token,
            required_scope="books:write",
            ui_client=identity.ui_client,
        )
        result = intake_book(
            db,
            IntakeInput(
                isbn=isbn,
                title=title,
                author=author,
                image_path=image_path,
                price=price,
                channel=channel,
                location=location,
                member_id=resolved_member_id,
            ),
        )
        log_and_commit(
            db,
            action="book.intake",
            member_id=resolved_member_id,
            channel=identity.channel,
            payload={"book_id": result.book.id, "action": result.action, "isbn_detected": result.isbn_detected},
        )
        data, status_code = _build_intake_response(result)
        return data.model_dump(), status_code


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
    identity: ChannelIdentity = Depends(channel_headers),
) -> ApiResponse:
    image_path: Path | None = None
    temp_file: Path | None = None

    try:
        if image and image.filename:
            suffix = Path(image.filename).suffix or ".jpg"
            content = await read_upload_limited(image)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_file = Path(tmp.name)
                tmp.write(content)
            image_path = temp_file

        data, status_code = await run_in_threadpool(
            _run_intake_in_thread,
            identity=identity,
            isbn=isbn,
            title=title,
            author=author,
            image_path=image_path,
            price=price,
            channel=channel,
            location=location,
            member_id=member_id,
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

    response.status_code = status_code
    return ApiResponse(data=data)


@router.post("/intake/json", response_model=ApiResponse)
def intake_json(
    payload: IntakeRequest,
    response: Response,
    identity: ChannelIdentity = Depends(channel_headers),
    db: Session = Depends(get_db),
) -> ApiResponse:
    resolved_member_id = enforce_channel_member(
        db,
        body_member_id=payload.member_id,
        channel=identity.channel,
        external_user_id=identity.external_user_id,
        authorization=identity.authorization,
        web_session_token=identity.web_session_token,
        required_scope="books:write",
        ui_client=identity.ui_client,
    )
    payload = payload.model_copy(update={"member_id": resolved_member_id})
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
                member_id=resolved_member_id,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    log_and_commit(
        db,
        action="book.intake",
        member_id=resolved_member_id,
        channel=identity.channel,
        payload={"book_id": result.book.id, "action": result.action, "isbn_detected": result.isbn_detected},
    )
    data, status_code = _build_intake_response(result)
    response.status_code = status_code
    return ApiResponse(data=data.model_dump())
