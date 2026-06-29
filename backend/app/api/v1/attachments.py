import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import get_db
from app.schemas.attachment import AttachmentCreate, AttachmentOut
from app.schemas.book import ApiResponse
from app.services.attachments import create_attachment
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_operation

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.post("", response_model=ApiResponse, status_code=201)
async def add_attachment(
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    attach_type: str = Form(...),
    title: str | None = Form(default=None),
    url: str | None = Form(default=None),
    content_md: str | None = Form(default=None),
    mime_type: str | None = Form(default=None),
    sort_order: int = Form(default=0),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse:
    temp_file: Path | None = None
    try:
        try:
            payload = AttachmentCreate(
                entity_type=entity_type,
                entity_id=entity_id,
                attach_type=attach_type,
                title=title,
                url=url,
                content_md=content_md,
                mime_type=mime_type or (file.content_type if file else None),
                sort_order=sort_order,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        if file and file.filename:
            suffix = Path(file.filename).suffix or ".bin"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_file = Path(tmp.name)
                tmp.write(await file.read())

        try:
            result = await run_in_threadpool(create_attachment, db, payload, upload_path=temp_file)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    log_operation(db, action="attachment.create", payload={"attachment_id": result.attachment.id})
    db.commit()
    data = AttachmentOut.model_validate(result.attachment).model_dump()
    data["message"] = result.message
    return ApiResponse(data=data)
