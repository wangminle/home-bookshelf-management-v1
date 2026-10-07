"""LOC-06：/storage 位置结构路由。

门禁（M0 冻结契约 LOC-01/LOC-02）：
- 读端点：require_scope("locations:read")（Owner/Member Web 与显式授权的
  Agent Token；匿名 401）；
- 写端点：仅 Owner Web 会话（_require_owner + verify_csrf），第一阶段不向
  Member/Token 开放结构化位置写。
- 位置域错误以 LocationError.as_http_exception() 的结构化 detail 返回；
  旧接口的字符串 detail 约定不受影响。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.v1.files import _DANGEROUS_MIME, _should_force_download
from app.auth_context import AuthContext, require_scope, verify_csrf
from app.db import get_db
from app.schemas.book import ApiResponse
from app.schemas.storage import (
    IntakeCommitIn,
    LayoutPut,
    PhotoUpdate,
    PlacementExecuteIn,
    PlacementPreviewIn,
    RoomCreate,
    RoomUpdate,
    ShelfCreate,
    ShelfUpdate,
)
from app.services import storage_intake as intake_svc
from app.services import storage_locations as svc
from app.services import storage_photos as photos_svc
from app.services import storage_placement as placement_svc
from app.services import storage_queries as queries
from app.services.storage_tx import LocationError
from app.utils.uploads import read_upload_limited

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage", tags=["storage"])


def _require_owner(request: Request, db: Session = Depends(get_db)):
    """Owner-only 依赖（延迟导入避免与 web_auth 循环引用，同 books.py 范本）。"""
    from app.api.v1.web_auth import require_owner

    return require_owner(request, db)


def _invoke(fn, *args, **kwargs):
    """位置域错误 → 结构化 detail 的 HTTPException。"""
    try:
        return fn(*args, **kwargs)
    except LocationError as exc:
        raise exc.as_http_exception() from exc


# ── 房间 ──

@router.get("/rooms", response_model=ApiResponse)
def list_rooms(
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("locations:read")),
) -> ApiResponse:
    data = svc.list_rooms(db, include_archived=include_archived, limit=limit, offset=offset)
    # LOC-08：每房间副本统计（聚合查询，不随房间数逐行扩张）
    stats = queries.rooms_stats(db, [item["id"] for item in data["items"]])
    for item in data["items"]:
        item["stats"] = stats.get(item["id"], queries._zero_stats())
    return ApiResponse(data=data)


@router.post("/rooms", response_model=ApiResponse, status_code=201)
def create_room(
    payload: RoomCreate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    result = _invoke(svc.create_room, db, payload, operator_member_id=owner.id)
    return ApiResponse(data=result["room"])


@router.patch("/rooms/{room_id}", response_model=ApiResponse)
def update_room(
    room_id: int,
    payload: RoomUpdate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    result = _invoke(svc.update_room, db, room_id, payload, operator_member_id=owner.id)
    return ApiResponse(data=result["room"])


# ── 书架 ──

@router.get("/shelves", response_model=ApiResponse)
def list_shelves(
    room_id: int | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("locations:read")),
) -> ApiResponse:
    return ApiResponse(data=svc.list_shelves(
        db, room_id=room_id, include_archived=include_archived,
        limit=limit, offset=offset))


@router.post("/shelves", response_model=ApiResponse, status_code=201)
def create_shelf(
    payload: ShelfCreate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    result = _invoke(svc.create_shelf, db, payload, operator_member_id=owner.id)
    return ApiResponse(data=result["shelf"])


@router.get("/shelves/{shelf_id}", response_model=ApiResponse)
def get_shelf(
    shelf_id: int,
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("locations:read")),
) -> ApiResponse:
    data = _invoke(svc.get_shelf_detail, db, shelf_id, include_archived=include_archived)
    # LOC-08：§9 书架详情含统计（§6.3 口径）
    data["stats"] = queries.shelf_stats(db, shelf_id)
    # LOC-09：§9 书架详情含受授权的照片引用（每架 ≤5 张，一次取全）
    data["photos"] = photos_svc.list_photos(db, shelf_id, limit=100, offset=0)["items"]
    return ApiResponse(data=data)


@router.patch("/shelves/{shelf_id}", response_model=ApiResponse)
def update_shelf(
    shelf_id: int,
    payload: ShelfUpdate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    result = _invoke(svc.update_shelf, db, shelf_id, payload, operator_member_id=owner.id)
    return ApiResponse(data=result["shelf"])


@router.put("/shelves/{shelf_id}/layout", response_model=ApiResponse)
def put_layout(
    shelf_id: int,
    payload: LayoutPut,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    result = _invoke(svc.put_layout, db, shelf_id, payload, operator_member_id=owner.id)
    return ApiResponse(data=result["shelf"])


# ── 格子副本清单与未定位清单（LOC-08） ──

@router.get("/cells/{cell_id}/copies", response_model=ApiResponse)
def list_cell_copies(
    cell_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("locations:read")),
) -> ApiResponse:
    """格子副本清单：副本摘要 + 书目 + 归属成员 + 实时位置路径。"""
    return ApiResponse(data=_invoke(
        queries.list_cell_copies, db, cell_id, limit=limit, offset=offset))


@router.get("/unlocated", response_model=ApiResponse)
def list_unlocated(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("locations:read")),
) -> ApiResponse:
    """三类未定位清单（§5.1，互不混算；每类独立 items+total）。"""
    return ApiResponse(data=queries.unlocated(db, limit=limit, offset=offset))


# ── 照片（LOC-09，设计 §8/§9） ──

@router.post("/shelves/{shelf_id}/photos", response_model=ApiResponse, status_code=201)
async def upload_shelf_photo(
    shelf_id: int,
    image: UploadFile = File(...),
    caption: str | None = Form(default=None, max_length=200),
    is_primary: bool = Form(default=False),
    idempotency_key: str = Form(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """上传书架照片（multipart）。仅 Owner Web；字节超限统一为结构化 PHOTO_TOO_LARGE。"""
    try:
        data = await read_upload_limited(image, max_bytes=photos_svc.MAX_PHOTO_BYTES)
    except HTTPException as exc:
        if exc.status_code == 413:
            raise photos_svc.PhotoTooLarge().as_http_exception() from exc
        raise
    result = _invoke(photos_svc.upload_photo, db, shelf_id, data=data,
                     caption=caption, is_primary=is_primary,
                     idempotency_key=idempotency_key, operator_member_id=owner.id)
    return ApiResponse(data=result["photo"])


@router.get("/shelves/{shelf_id}/photos", response_model=ApiResponse)
def list_shelf_photos(
    shelf_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _ctx: AuthContext = Depends(require_scope("locations:read")),
) -> ApiResponse:
    """书架照片清单（分页；契约修订记录 LOC-09 新增的清单端点）。"""
    return ApiResponse(data=_invoke(
        photos_svc.list_photos, db, shelf_id, limit=limit, offset=offset))


@router.get("/photos/{photo_id}/content")
def get_photo_content(
    photo_id: int,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_scope("locations:read")),
) -> FileResponse:
    """照片内容：§9 门禁要求 locations:read + files:read 双 scope。"""
    ctx.require_scope("files:read")
    photo = _invoke(photos_svc.get_photo, db, photo_id)  # 404 防枚举
    path = photos_svc.resolve_photo_path(photo.relative_path)
    if path is None:
        logger.warning("照片文件缺失或路径越界: photo_id=%s path=%s",
                       photo_id, photo.relative_path)
        raise HTTPException(status_code=404, detail="照片文件不可用")
    # 沿用 files.py 口径：危险后缀强制下载（photos 白名单不含危险后缀，防御性保留）
    if _should_force_download(path):
        return FileResponse(path, filename=path.name,
                            content_disposition_type="attachment",
                            media_type=_DANGEROUS_MIME)
    return FileResponse(path, media_type=photo.mime_type)


@router.patch("/photos/{photo_id}", response_model=ApiResponse)
def update_shelf_photo(
    photo_id: int,
    payload: PhotoUpdate,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    result = _invoke(photos_svc.update_photo, db, photo_id, payload,
                     operator_member_id=owner.id)
    return ApiResponse(data=result["photo"])


@router.delete("/photos/{photo_id}", response_model=ApiResponse)
def delete_shelf_photo(
    photo_id: int,
    version: int = Query(...),
    idempotency_key: str | None = Query(default=None, min_length=1, max_length=100),
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """删除照片；期望版本走 query（DELETE 不带请求体，契约修订记录 LOC-09）。"""
    result = _invoke(photos_svc.delete_photo, db, photo_id, version=version,
                     idempotency_key=idempotency_key, operator_member_id=owner.id)
    return ApiResponse(data=result)


# ── 批量移动（LOC-13，设计 §7.2/§9） ──

@router.post("/placements/preview", response_model=ApiResponse)
def preview_placements(
    payload: PlacementPreviewIn,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """批量移动预览（dry-run 不落库）：每册源→目标与将发生的版本变化 + 预览摘要。"""
    return ApiResponse(data=_invoke(placement_svc.preview_move, db, payload))


@router.post("/placements", response_model=ApiResponse)
def execute_placements(
    payload: PlacementExecuteIn,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """原子执行批量移动：重验预览摘要与全部期望版本，任一失效整批拒绝零写入。"""
    return ApiResponse(data=_invoke(
        placement_svc.execute_move, db, payload, operator_member_id=owner.id))


# ── 存量副本补录（LOC-14，设计 §7.2/§10.1） ──

@router.post("/copies", response_model=ApiResponse, status_code=201)
def commit_intake(
    payload: IntakeCommitIn,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
    _csrf: None = Depends(verify_csrf),
) -> ApiResponse:
    """批量补录实体副本及位置：逐项为已有副本分配位置、显式确认新增册数与归属。

    原子事务：任一条目失效整批零写入；同 key 重放不重复创建（§12 验收）。
    """
    return ApiResponse(data=_invoke(
        intake_svc.commit_intake, db, payload, operator_member_id=owner.id))


# ── 写操作回执 ──

@router.get("/operations/{idempotency_key}", response_model=ApiResponse)
def get_operation(
    idempotency_key: str,
    db: Session = Depends(get_db),
    owner=Depends(_require_owner),
) -> ApiResponse:
    """查询自己的写操作回执（completed / not_found；not_found 不等于确定未执行）。"""
    return ApiResponse(data=svc.get_operation_receipt(
        db, idempotency_key, operator_member_id=owner.id))
