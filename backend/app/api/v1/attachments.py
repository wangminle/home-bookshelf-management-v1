import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app import db as db_module
from app.auth import ChannelIdentity, channel_headers, enforce_channel_member, system_has_channel_bindings
from app.schemas.attachment import AttachmentCreate, AttachmentOut
from app.schemas.book import ApiResponse
from app.services.attachments import create_attachment
from app.utils.db_errors import ConflictError
from app.utils.operation_log import log_and_commit
from app.utils.uploads import read_upload_limited

router = APIRouter(prefix="/attachments", tags=["attachments"])


def _create_attachment_in_thread(
    *,
    payload: AttachmentCreate,
    upload_path: Path | None,
    identity: ChannelIdentity,
) -> dict:
    with db_module.SessionLocal() as db:
        # BUG-115：白名单建立后拒绝匿名回退；引导期（无绑定）仍允许匿名
        require_channel = system_has_channel_bindings(db)
        member_id = enforce_channel_member(
            db,
            body_member_id=None,
            channel=identity.channel,
            external_user_id=identity.external_user_id,

            ui_client=identity.ui_client,
            require_channel=require_channel,
        )
        result = create_attachment(db, payload, upload_path=upload_path)
        log_and_commit(
            db,
            action="attachment.create",
            member_id=member_id,
            channel=identity.channel,
            payload={"attachment_id": result.attachment.id},
        )
        data = AttachmentOut.model_validate(result.attachment).model_dump()
        data["message"] = result.message
        return data


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
    identity: ChannelIdentity = Depends(channel_headers),
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
            content = await read_upload_limited(file)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_file = Path(tmp.name)
                tmp.write(content)

        try:
            data = await run_in_threadpool(
                _create_attachment_in_thread,
                payload=payload,
                upload_path=temp_file,
                identity=identity,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    return ApiResponse(data=data)
