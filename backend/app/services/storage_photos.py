"""LOC-09：书架照片领域服务与受控文件回收（设计 §8/§9，M0 冻结契约修订记录）。

纪律：
- 上传：先写暂存文件再进事务；事务失败仅清理本次暂存文件；commit 后暂存转正
  （原文件名不参与路径，relative_path = shelf_photos/<shelf_id>/<uuid>.<ext>）。
- 服务端验真实格式与尺寸（PIL 解码），重编码去 EXIF；JPEG/PNG/WebP 白名单，
  单张 ≤10MiB、解码像素 ≤2000 万、每架 ≤5 张、每架最多一张主图。
- 删除：DB 行删除与 location_file_gc_jobs 登记同事务；commit 后 best-effort
  立即回收，进程中断由下次启动 resume_gc_jobs 继续。
- 回收任务：条件更新取短期租约；执行前重验 containment（必须在 shelf_photos
  目录内）与无引用；删除不存在文件视为完成；失败 attempts+指数退避，超上限
  保留 failed 待人工处理。
- 孤儿核对（orphan_audit）只读：仅扫 shelf_photos_dir，不触碰 covers/
  attachments；>24h 无引用才列入清单，供人工确认，不自动删。
- 写路径复用 storage_tx 原语（幂等回执、锁序、版本乐观锁、受限重试），
  审计只调 log_operation（同事务）。
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import StorageRoom, StorageShelf
from app.models.storage import LocationFileGcJob, ShelfPhoto
from app.schemas.storage import PhotoOut, PhotoUpdate
from app.services import storage_tx
from app.services.storage_tx import LocationArchived, LocationError, NotFound
from app.utils.operation_log import log_operation

logger = logging.getLogger(__name__)

# 设计 §8 上限（M0 冻结 + LOC-09 修订记录）
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 单张 ≤10MiB
MAX_PHOTO_PIXELS = 20_000_000  # 解码像素 ≤2000 万
MAX_PHOTOS_PER_SHELF = 5

_ALLOWED_FORMATS = {"JPEG": ("image/jpeg", ".jpg"),
                    "PNG": ("image/png", ".png"),
                    "WEBP": ("image/webp", ".webp")}

_GC_LEASE = timedelta(minutes=5)
_GC_MAX_ATTEMPTS = 5


class InvalidImage(LocationError):
    def __init__(self, message: str = "照片内容不是合法的 JPEG/PNG/WebP 图像") -> None:
        super().__init__("INVALID_IMAGE", message, 422)


class PhotoTooLarge(LocationError):
    def __init__(self, message: str = "照片超过大小或像素上限") -> None:
        super().__init__("PHOTO_TOO_LARGE", message, 413)


class PhotoLimitExceeded(LocationError):
    def __init__(self, message: str = f"每个书架最多 {MAX_PHOTOS_PER_SHELF} 张照片") -> None:
        super().__init__("PHOTO_LIMIT_EXCEEDED", message, 422)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_and_reencode(data: bytes) -> tuple[bytes, str, int, int]:
    """解码校验真实格式/尺寸，重编码去 EXIF；返回 (字节, mime, 宽, 高)。"""
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoTooLarge(
            f"照片超过大小上限（{MAX_PHOTO_BYTES // (1024 * 1024)}MiB）")
    try:
        img = Image.open(BytesIO(data))
    except Exception as exc:
        raise InvalidImage() from exc
    if img.format not in _ALLOWED_FORMATS:
        raise InvalidImage(f"不支持的图像格式 {img.format!r}，仅接受 JPEG/PNG/WebP")
    fmt = img.format
    width, height = img.width, img.height
    # 在 load() 分配像素缓冲前挡掉超限图（避免解压炸弹式内存占用）
    if width * height > MAX_PHOTO_PIXELS:
        raise PhotoTooLarge(
            f"照片解码像素 {width * height} 超过上限 {MAX_PHOTO_PIXELS}")
    try:
        img.load()
    except Exception as exc:
        raise InvalidImage("图像解码失败（内容损坏或与扩展名不符）") from exc

    # 模式归一：JPEG 无透明通道；WebP 仅 RGB/RGBA；PNG 调色板模式转真彩
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif fmt == "WEBP" and img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif fmt == "PNG" and img.mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")

    # 重编码去 EXIF：新建空 info 的同尺寸画布粘贴像素，save 不带任何元数据
    clean = Image.new(img.mode, img.size)
    clean.paste(img)
    buf = BytesIO()
    save_kwargs = {"quality": 90} if fmt in ("JPEG", "WEBP") else {}
    clean.save(buf, format=fmt, **save_kwargs)
    mime, _ext = _ALLOWED_FORMATS[fmt]
    return buf.getvalue(), mime, width, height


def resolve_photo_path(relative_path: str) -> Path | None:
    """解析存储相对路径为绝对路径；逃出 shelf_photos 目录或文件不存在返回 None。"""
    base = settings.shelf_photos_dir.resolve()
    target = (settings.data_dir / relative_path).resolve()
    if not target.is_relative_to(base):
        return None
    if not target.is_file():
        return None
    return target


def _photo_out(photo: ShelfPhoto) -> dict:
    return PhotoOut.model_validate(photo).model_dump(mode="json")


# ── 读 ──

def get_photo(db: Session, photo_id: int) -> ShelfPhoto:
    photo = db.get(ShelfPhoto, photo_id)
    if photo is None:
        raise NotFound(f"照片 {photo_id} 不存在")
    return photo


def list_photos(db: Session, shelf_id: int, *, limit: int, offset: int) -> dict:
    if db.get(StorageShelf, shelf_id) is None:
        raise NotFound(f"书架 {shelf_id} 不存在")
    q = select(ShelfPhoto).where(ShelfPhoto.shelf_id == shelf_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(ShelfPhoto.id).limit(limit).offset(offset)).all()
    return {"items": [_photo_out(p) for p in rows], "total": total}


# ── 写：上传 ──

def upload_photo(
    db: Session,
    shelf_id: int,
    *,
    data: bytes,
    caption: str | None,
    is_primary: bool,
    idempotency_key: str,
    operator_member_id: int,
) -> dict:
    """上传书架照片：先解码重编码 → 写暂存 → 事务落库 → commit 后暂存转正。

    事务失败/幂等重放仅清理本次暂存文件，绝不动既有照片文件。
    """
    clean_bytes, mime, width, height = _decode_and_reencode(data)
    ext = {m: e for m, e in _ALLOWED_FORMATS.values()}[mime]
    shelf_dir = settings.shelf_photos_dir / str(shelf_id)
    shelf_dir.mkdir(parents=True, exist_ok=True)
    staged = shelf_dir / f"tmp-{uuid.uuid4().hex}{ext}"
    staged.write_bytes(clean_bytes)

    replayed = False
    final_rel: str | None = None
    digest_payload = {
        "op": "storage.photo.upload",
        "shelf_id": shelf_id,
        "sha256": hashlib.sha256(data).hexdigest(),
        "caption": caption,
        "is_primary": is_primary,
    }

    def fn(db: Session) -> dict:
        nonlocal replayed, final_rel
        begin = storage_tx.begin_operation(
            db, operator_member_id=operator_member_id,
            idempotency_key=idempotency_key, payload=digest_payload)
        if begin.replayed:
            replayed = True
            return begin.result
        resources = storage_tx.collect_ancestors(db, shelf_ids=[shelf_id])
        storage_tx.lock_resources(db, resources)
        shelf = db.get(StorageShelf, shelf_id)
        room = db.get(StorageRoom, shelf.room_id)
        if room.archived_at is not None:
            raise LocationArchived(f"上级房间 {room.id} 已归档，书架只读")
        if shelf.archived_at is not None:
            raise LocationArchived(f"书架 {shelf_id} 已归档，不能上传照片")
        count = db.scalar(
            select(func.count()).select_from(ShelfPhoto)
            .where(ShelfPhoto.shelf_id == shelf_id)) or 0
        if count >= MAX_PHOTOS_PER_SHELF:
            raise PhotoLimitExceeded()
        final_rel = f"shelf_photos/{shelf_id}/{uuid.uuid4().hex}{ext}"
        # 契约修订（LOC-09）：架内首张照片无论请求值一律成为主图
        primary = bool(is_primary) or count == 0
        if primary:
            db.execute(
                update(ShelfPhoto)
                .where(ShelfPhoto.shelf_id == shelf_id, ShelfPhoto.is_primary.is_(True))
                .values(is_primary=False))
        photo = ShelfPhoto(
            shelf_id=shelf_id, relative_path=final_rel, mime_type=mime,
            width=width, height=height, caption=caption, is_primary=primary)
        db.add(photo)
        db.flush()
        db.refresh(photo)
        log_operation(db, action="storage.photo.upload", member_id=operator_member_id,
                      payload={"operation_key": idempotency_key,
                               "operation_id": begin.operation.id,
                               "photo_id": photo.id, "shelf_id": shelf_id,
                               "relative_path": final_rel, "is_primary": primary})
        result = {"photo": _photo_out(photo)}
        storage_tx.complete_operation(db, begin.operation, result)
        db.commit()
        return result

    try:
        result = storage_tx.run_in_txn(db, fn)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    if replayed:
        # 重放已存回执：本次暂存文件不属于任何照片记录，仅清理本次文件
        staged.unlink(missing_ok=True)
    else:
        staged.replace(settings.data_dir / final_rel)
    return result


# ── 写：改标注 / 设主图 ──

def update_photo(
    db: Session,
    photo_id: int,
    payload: PhotoUpdate,
    *,
    operator_member_id: int,
) -> dict:
    fields = payload.model_dump(exclude_unset=True,
                                exclude={"idempotency_key", "version"})
    digest_payload = {"op": "storage.photo.update", "photo_id": photo_id,
                      "fields": fields}

    def fn(db: Session) -> dict:
        begin = None
        if payload.idempotency_key:
            begin = storage_tx.begin_operation(
                db, operator_member_id=operator_member_id,
                idempotency_key=payload.idempotency_key, payload=digest_payload)
            if begin.replayed:
                return begin.result
        photo = db.get(ShelfPhoto, photo_id)
        if photo is None:
            raise NotFound(f"照片 {photo_id} 不存在")
        resources = storage_tx.collect_ancestors(db, shelf_ids=[photo.shelf_id])
        storage_tx.lock_resources(db, resources)
        shelf = db.get(StorageShelf, photo.shelf_id)
        room = db.get(StorageRoom, shelf.room_id)
        if room.archived_at is not None or shelf.archived_at is not None:
            raise LocationArchived(f"照片 {photo_id} 所在书架已归档，只读")

        storage_tx.bump_version(db, ShelfPhoto, photo_id, payload.version)
        if "caption" in fields:
            photo.caption = fields["caption"]
        if fields.get("is_primary") is True:
            db.execute(
                update(ShelfPhoto)
                .where(ShelfPhoto.shelf_id == photo.shelf_id,
                       ShelfPhoto.is_primary.is_(True),
                       ShelfPhoto.id != photo.id)
                .values(is_primary=False))
            photo.is_primary = True
        elif fields.get("is_primary") is False:
            photo.is_primary = False
        db.flush()
        db.refresh(photo)
        log_operation(db, action="storage.photo.update", member_id=operator_member_id,
                      payload={"operation_key": payload.idempotency_key,
                               "operation_id": begin.operation.id if begin else None,
                               "photo_id": photo_id, "shelf_id": photo.shelf_id,
                               "fields": fields, "version": payload.version})
        result = {"photo": _photo_out(photo)}
        if begin is not None:
            storage_tx.complete_operation(db, begin.operation, result)
        db.commit()
        return result

    return storage_tx.run_in_txn(db, fn)


# ── 写：删除（DB 行与 GC 登记同事务，commit 后 best-effort 回收） ──

def delete_photo(
    db: Session,
    photo_id: int,
    *,
    version: int,
    idempotency_key: str | None = None,
    operator_member_id: int,
) -> dict:
    digest_payload = {"op": "storage.photo.delete", "photo_id": photo_id}

    def fn(db: Session) -> dict:
        begin = None
        if idempotency_key:
            begin = storage_tx.begin_operation(
                db, operator_member_id=operator_member_id,
                idempotency_key=idempotency_key, payload=digest_payload)
            if begin.replayed:
                return begin.result
        photo = db.get(ShelfPhoto, photo_id)
        if photo is None:
            raise NotFound(f"照片 {photo_id} 不存在")
        resources = storage_tx.collect_ancestors(db, shelf_ids=[photo.shelf_id])
        storage_tx.lock_resources(db, resources)
        shelf = db.get(StorageShelf, photo.shelf_id)
        room = db.get(StorageRoom, shelf.room_id)
        if room.archived_at is not None or shelf.archived_at is not None:
            raise LocationArchived(f"照片 {photo_id} 所在书架已归档，只读")

        storage_tx.bump_version(db, ShelfPhoto, photo_id, version)
        rel = photo.relative_path
        shelf_id = photo.shelf_id
        db.delete(photo)
        db.add(LocationFileGcJob(relative_path=rel, reason="photo_deleted"))
        log_operation(db, action="storage.photo.delete", member_id=operator_member_id,
                      payload={"operation_key": idempotency_key,
                               "operation_id": begin.operation.id if begin else None,
                               "photo_id": photo_id, "shelf_id": shelf_id,
                               "relative_path": rel, "version": version})
        result = {"photo_id": photo_id, "deleted": True}
        if begin is not None:
            storage_tx.complete_operation(db, begin.operation, result)
        db.commit()
        return result

    result = storage_tx.run_in_txn(db, fn)
    try:
        process_gc_jobs(db)
    except Exception:  # best-effort：失败由启动恢复/受控清理继续
        logger.exception("照片删除后的即时文件回收失败（将由启动恢复重试）")
    return result


# ── 文件回收（租约 + 重验 + 退避） ──

def _gc_fail(db: Session, job: LocationFileGcJob, error: str) -> str:
    """失败处理：attempts 超上限保留 failed，否则回 pending + 指数退避。"""
    job.last_error = error
    job.lease_until = None
    if job.attempts >= _GC_MAX_ATTEMPTS:
        job.status = "failed"
        job.next_retry_at = None
    else:
        job.status = "pending"
        job.next_retry_at = _now() + timedelta(minutes=2 ** job.attempts)
    db.commit()
    return job.status


def process_gc_jobs(db: Session, *, limit: int = 50) -> dict:
    """处理到期的文件回收任务。幂等、可重入；崩溃留下的 processing 任务
    在租约到期后被重新领取。"""
    summary = {"processed": 0, "done": 0, "failed": 0, "skipped": 0}
    now = _now()
    candidates = db.scalars(
        select(LocationFileGcJob)
        .where(LocationFileGcJob.status.in_(("pending", "processing")),
               (LocationFileGcJob.lease_until.is_(None))
               | (LocationFileGcJob.lease_until < now),
               (LocationFileGcJob.next_retry_at.is_(None))
               | (LocationFileGcJob.next_retry_at <= now))
        .order_by(LocationFileGcJob.id)
        .limit(limit)).all()

    for candidate in candidates:
        # 条件更新取短期租约（多实例/重入安全）：rowcount=0 表示已被他人领走
        taken = db.execute(
            update(LocationFileGcJob)
            .execution_options(synchronize_session=False)
            .where(LocationFileGcJob.id == candidate.id,
                   LocationFileGcJob.status.in_(("pending", "processing")),
                   (LocationFileGcJob.lease_until.is_(None))
                   | (LocationFileGcJob.lease_until < now))
            .values(status="processing",
                    lease_until=now + _GC_LEASE,
                    attempts=LocationFileGcJob.attempts + 1))
        if taken.rowcount != 1:
            db.rollback()
            summary["skipped"] += 1
            continue
        db.commit()  # 租约先落库：崩溃后他人在租约到期前不会重复领取
        summary["processed"] += 1
        job = db.get(LocationFileGcJob, candidate.id)
        try:
            # 执行前重验 containment：目标必须仍在 shelf_photos 目录内
            base = settings.shelf_photos_dir.resolve()
            target = (settings.data_dir / job.relative_path).resolve()
            if not target.is_relative_to(base):
                job.status = "failed"
                job.lease_until = None
                job.last_error = f"路径越出 shelf_photos 目录，拒绝删除: {job.relative_path}"
                db.commit()
                summary["failed"] += 1
                continue
            # 执行前重验无引用：仍被照片记录引用的文件绝不动
            referenced = db.scalar(
                select(func.count()).select_from(ShelfPhoto)
                .where(ShelfPhoto.relative_path == job.relative_path)) or 0
            if referenced:
                job.status = "failed"
                job.lease_until = None
                job.last_error = "文件仍被照片记录引用，未删除（需人工核对）"
                db.commit()
                summary["failed"] += 1
                continue
            target.unlink(missing_ok=True)  # 删除不存在文件视为完成
            job.status = "done"
            job.lease_until = None
            job.last_error = None
            db.commit()
            summary["done"] += 1
        except Exception as exc:  # 文件系统错误等：退避重试 / 超上限 failed
            db.rollback()
            job = db.get(LocationFileGcJob, candidate.id)
            final = _gc_fail(db, job, f"{type(exc).__name__}: {exc}")
            summary["failed" if final == "failed" else "skipped"] += 1
    return summary


def resume_gc_jobs() -> None:
    """进程启动恢复：继续处理中断的回收任务。失败只记日志，不阻塞启动。

    SessionLocal 经模块属性引用：测试夹具按用例重绑 app.db.SessionLocal。
    """
    from app import db as db_module

    session = db_module.SessionLocal()
    try:
        summary = process_gc_jobs(session)
        if summary["processed"]:
            logger.info("位置文件回收启动恢复: %s", summary)
    except Exception:
        logger.exception("位置文件回收启动恢复失败")
    finally:
        session.close()


# ── 孤儿核对（只读；不扫描 covers/attachments；不自动删） ──

def orphan_audit(db: Session, *, older_than_hours: int = 24) -> list[dict]:
    """列出 shelf_photos 目录下超过 older_than_hours 无 DB 引用的文件。

    只读清单，供人工确认后手动清理；本函数不删除任何文件。
    """
    base = settings.shelf_photos_dir
    if not base.is_dir():
        return []
    referenced = set(db.scalars(select(ShelfPhoto.relative_path)).all())
    cutoff = time.time() - older_than_hours * 3600
    orphans: list[dict] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        full_rel = f"shelf_photos/{path.relative_to(base).as_posix()}"
        if full_rel in referenced:
            continue
        stat = path.stat()
        if stat.st_mtime > cutoff:
            continue  # 新近文件可能在「暂存未转正」窗口内，不算孤儿
        orphans.append({
            "relative_path": full_rel,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return sorted(orphans, key=lambda item: item["relative_path"])
