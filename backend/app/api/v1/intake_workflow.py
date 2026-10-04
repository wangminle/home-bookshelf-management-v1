"""拍照批量入库工作台 API（PLN-012 M3 / BI-16 后端）。

权限（规划 §4.8）：
- 全部端点仅 Owner Web 会话（require_owner）；写操作校验 CSRF；
- Member/Agent/匿名一律 401/403——工作台不随 books:write 开放；
- 候选图片走本路由的 Owner 会话读取（BUG-255：Member/Agent 403），不复用匿名封面入口；
- 每次执行经 require_owner 重新鉴权（停用/撤权即拒绝），不转借 Cookie。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_context import AuthContext, require_auth, verify_csrf
from app.db import get_db
from app.models import IntakeCandidate, IntakeChangeSet, IntakeCommandExecution, IntakePhoto, IntakeWorkItem
from app.schemas.book import ApiResponse
from app.services import intake_workflow as wf
from app.services.intake_workflow import WorkflowError
from app.utils.operation_log import log_and_commit
from app.utils.uploads import read_upload_limited

router = APIRouter(prefix="/intake-workflow", tags=["intake-workflow"])


def _require_owner(request: Request, db: Session = Depends(get_db)):
    from app.api.v1.web_auth import require_owner

    return require_owner(request, db)


def _wf_error(exc: WorkflowError) -> HTTPException:
    message = str(exc)
    if isinstance(exc, wf.WorkflowAuthError):
        return HTTPException(status_code=403, detail=message)  # BUG-256：停用/撤权
    status = 409 if ("已存在" in message or "不可" in message) else 400
    return HTTPException(status_code=status, detail=message)


def _load_item_snapshot(db: Session, item: IntakeWorkItem) -> dict:
    photos = db.scalars(select(IntakePhoto).where(
        IntakePhoto.work_item_id == item.id)).fetchall()
    candidates = db.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == item.id)).fetchall()
    executions = db.scalars(select(IntakeCommandExecution).join(IntakeChangeSet).where(
        IntakeChangeSet.work_item_id == item.id)).fetchall()
    return wf.work_item_to_dict(item, photos=photos, candidates=candidates,
                                executions=executions)


@router.get("/work-items", response_model=ApiResponse)
def list_work_items(
    owner=Depends(_require_owner),
    db: Session = Depends(get_db),
) -> ApiResponse:
    items = db.scalars(select(IntakeWorkItem).order_by(
        IntakeWorkItem.id.desc())).fetchall()
    data = []
    for item in items:
        has_photos = db.scalar(select(IntakePhoto.id).where(
            IntakePhoto.work_item_id == item.id).limit(1)) is not None
        data.append({
            "id": item.id, "title": item.title, "status": item.status,
            "has_photos": has_photos,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        })
    return ApiResponse(data={"items": data})


@router.post("/work-items", response_model=ApiResponse)
def create_work_item(
    payload: dict,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    item = wf.create_work_item(db, title=str(payload.get("title") or ""),
                               created_by_member_id=owner.id)
    log_and_commit(db, action="intake_workflow.work_item.create", member_id=owner.id,
                   channel="web", payload={"work_item_id": item.id})
    return ApiResponse(data={"id": item.id, "status": item.status})


@router.get("/work-items/{work_item_id}", response_model=ApiResponse)
def get_work_item(
    work_item_id: int,
    owner=Depends(_require_owner),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        item = wf.get_work_item(db, work_item_id)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    return ApiResponse(data=_load_item_snapshot(db, item))


@router.post("/work-items/{work_item_id}/photos", response_model=ApiResponse)
async def upload_photos(
    work_item_id: int,
    request: Request,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """multipart 上传：photo_ids（逗号分隔，可选）与 files 按顺序一一对应。"""
    try:
        form = await request.form()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"表单解析失败：{exc}") from exc
    pids_raw = form.get("photo_ids")
    photo_ids = [p.strip() for p in str(pids_raw).split(",") if p.strip()] if pids_raw else []
    files = list(form.getlist("files"))
    if not files:
        # BUG-249：空文件/无文件请求必须明确失败，不得 200 + added=[]
        raise HTTPException(status_code=400, detail="未收到任何上传文件")
    if not photo_ids:
        # BUG-269：省略 photo_ids 时从既有最大 p<序号> 续号，第二轮上传不冲突
        start = wf.next_photo_sequence(db, work_item_id)
        photo_ids = [f"p{start + i:04d}" for i in range(len(files))]
    if len(photo_ids) != len(files):
        raise HTTPException(status_code=400,
                            detail=f"photo_ids 数量({len(photo_ids)})与文件数({len(files)})不一致")
    uploads = []
    for pid, upload in zip(photo_ids, files):
        content = await read_upload_limited(upload)
        suffix = Path(upload.filename or "").suffix or ".jpg"
        uploads.append({"photo_id": pid, "filename": upload.filename or f"{pid}{suffix}",
                        "content": content, "role": "cover"})
    try:
        saved = wf.add_photos(db, work_item_id, uploads)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    log_and_commit(db, action="intake_workflow.photos.add", member_id=owner.id,
                   channel="web",
                   payload={"work_item_id": work_item_id, "count": len(saved)})
    return ApiResponse(data={"added": [wf.photo_to_dict(p) for p in saved]})


@router.post("/work-items/{work_item_id}/candidates", response_model=ApiResponse)
def build_candidates(
    work_item_id: int,
    payload: dict,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """按识别/手工结果构建候选（配对建议）。

    payload.recognized: {photo_id: {title, subtitle, authors, isbn,
    confidence, readable, warnings, source}}
    """
    fields = wf.RecognitionInput.__dataclass_fields__
    recognized = {
        str(pid): wf.RecognitionInput(**{k: v for k, v in (rec or {}).items() if k in fields})
        for pid, rec in (payload.get("recognized") or {}).items()
    }
    try:
        candidates = wf.build_candidates(db, work_item_id, recognized)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    log_and_commit(db, action="intake_workflow.candidates.build", member_id=owner.id,
                   channel="web",
                   payload={"work_item_id": work_item_id, "candidates": len(candidates)})
    return ApiResponse(data={"candidates": [wf.candidate_to_dict(c) for c in candidates]})


@router.patch("/candidates/{candidate_id}", response_model=ApiResponse)
def update_candidate(
    candidate_id: int,
    payload: dict,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    kwargs = {}
    if "title" in payload:
        kwargs["title"] = payload.get("title")
    if "subtitle" in payload:
        kwargs["subtitle"] = payload.get("subtitle")
    if "authors" in payload:
        kwargs["authors"] = payload.get("authors") or []
    if "isbn" in payload:
        kwargs["isbn"] = payload.get("isbn")
    if "photo_ids" in payload:
        kwargs["photo_ids"] = payload.get("photo_ids") or []
    try:
        cand = wf.update_candidate(db, candidate_id, **kwargs)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    log_and_commit(db, action="intake_workflow.candidate.update", member_id=owner.id,
                   channel="web", payload={"candidate_id": candidate_id,
                                           "version": cand.version})
    return ApiResponse(data=wf.candidate_to_dict(cand))


@router.post("/candidates/{candidate_id}/split", response_model=ApiResponse)
def split_candidate(
    candidate_id: int,
    payload: dict,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        original, new = wf.split_candidate(
            db, candidate_id, photo_ids_to_new=payload.get("photo_ids") or [])
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    return ApiResponse(data={"original": wf.candidate_to_dict(original),
                             "new": wf.candidate_to_dict(new)})


@router.post("/work-items/{work_item_id}/recognize", response_model=ApiResponse)
def recognize_photos(
    work_item_id: int,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """用统一视觉服务识别全部照片并重建候选（模型禁用时如实报错）。"""
    try:
        summary = wf.recognize_photos(db, work_item_id)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    log_and_commit(db, action="intake_workflow.recognize", member_id=owner.id,
                   channel="web",
                   payload={"work_item_id": work_item_id,
                            "failures": len(summary["failures"])})
    return ApiResponse(data=summary)


@router.post("/work-items/{work_item_id}/preview-matches", response_model=ApiResponse)
def preview_matches(
    work_item_id: int,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        results = wf.preview_matches(db, work_item_id)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    return ApiResponse(data={"matches": results})


@router.post("/work-items/{work_item_id}/confirm", response_model=ApiResponse)
def confirm(
    work_item_id: int,
    payload: dict,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        executions = wf.confirm_candidates(
            db, work_item_id, payload.get("candidate_ids") or [],
            decided_by_member_id=owner.id, note=payload.get("note"),
            resolutions=payload.get("resolutions"))  # BUG-254：显式人工解决冲突
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    log_and_commit(db, action="intake_workflow.confirm", member_id=owner.id,
                   channel="web",
                   payload={"work_item_id": work_item_id,
                            "candidates": payload.get("candidate_ids") or []})
    return ApiResponse(data={"executions": [wf.execution_to_dict(e) for e in executions]})


@router.post("/work-items/{work_item_id}/execute", response_model=ApiResponse)
def execute(
    work_item_id: int,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    # 执行前重新鉴权已由 require_owner 依赖完成；BUG-256：批内每条命令副作用前
    # 与提交时再核验执行者仍是有效 Owner（停用/撤权即 403，不执行后续命令）
    try:
        summary = wf.execute_work_item(db, work_item_id,
                                       requester_member_id=owner.id)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    log_and_commit(db, action="intake_workflow.execute", member_id=owner.id,
                   channel="web",
                   payload={"work_item_id": work_item_id, "status": summary["status"]})
    return ApiResponse(data=summary)


@router.post("/executions/{execution_id}/retry", response_model=ApiResponse)
def retry_execution(
    execution_id: int,
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        execution = wf.retry_execution(db, execution_id,
                                       requester_member_id=owner.id)
    except WorkflowError as exc:
        raise _wf_error(exc) from exc
    log_and_commit(db, action="intake_workflow.execution.retry", member_id=owner.id,
                   channel="web", payload={"execution_id": execution_id,
                                           "status": execution.status})
    return ApiResponse(data=wf.execution_to_dict(execution))


@router.get("/photos/{photo_id}/image")
def photo_image(
    photo_id: int,
    ctx: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> FileResponse:
    """受认证的照片读取：仅 Owner Web 会话（BUG-255，与任务端点一致的资源权限）。

    Member/Agent 一律 403，匿名 401；不复用匿名封面入口（规划 §4.8）。
    """
    if ctx.auth_type != "web" or not ctx.is_owner:
        raise HTTPException(status_code=403, detail="仅 owner 可读取工作台照片")
    photo = db.get(IntakePhoto, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="照片不存在")
    path = wf.photo_abs_path(photo)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="照片文件缺失")
    return FileResponse(path)
