import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app import db as db_module
from app.auth_context import AuthContext, require_scope, verify_csrf
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
    member_id: int,
    channel: str | None,
    operator_member_id: int | None = None,
) -> dict:
    """BUG-168：鉴权已在外层由 AuthContext 完成，线程内只做业务。"""
    with db_module.SessionLocal() as db:
        result = create_attachment(db, payload, upload_path=upload_path)
        log_and_commit(
            db,
            action="attachment.create",
            member_id=member_id,
            channel=channel,
            operator_member_id=operator_member_id,
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
    ctx: AuthContext = Depends(require_scope("notes:write")),
    _csrf: None = Depends(verify_csrf),
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
                # require_scope 已保证认证身份，member_id 必有值
                member_id=ctx.member_id,  # type: ignore[arg-type]
                channel=ctx.channel,
                operator_member_id=ctx.member_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    return ApiResponse(data=data)
