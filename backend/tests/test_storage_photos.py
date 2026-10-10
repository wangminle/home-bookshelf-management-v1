"""LOC-09：书架照片上传/读取/改删、受控文件接口与回收任务回归。

覆盖验收标准（设计 §8/§9 + M0 契约修订记录 LOC-09）：
- 上传：真实格式校验（伪扩展名 422）、字节上限 413、解码像素上限 413、
  每架 ≤5 张 422、重编码去 EXIF、首张自动主图、暂存失败仅清理本次文件；
- 读取：内容端点 locations:read + files:read 双 scope；路径穿越 404；
  书架详情含 photos 引用；清单分页；
- 改删：设主图并发最终恰好一张主图（真实线程）、版本乐观锁 409、
  删除同事务登记 GC 任务且 commit 后文件消失；
- 回收：中断恢复（手工造 pending 任务）、containment 越界拒删置 failed、
  仍被引用不动文件、删除不存在文件视为完成、孤儿核对只读；
- 审计与幂等：storage.photo.* 审计事件；同 key 同载荷重放返回原回执且零副作用。
"""
from __future__ import annotations

import os
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app.config import settings
from app.models import Member, OperationLog
from app.models.storage import LocationFileGcJob, ShelfPhoto
from app.schemas.storage import PhotoUpdate
from app.services import storage_photos
from app.services.storage_tx import PlacementChanged
from tests.test_bug166_167_168_auth import _agent_token
from tests.test_storage_auth import _member_web_client
from tests.test_storage_crud import _create_room, _create_shelf

# ── 基建 ──


def _jpeg_bytes(size=(64, 64), *, with_exif: bool = False) -> bytes:
    img = Image.new("RGB", size, (120, 30, 200))
    buf = BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[0x010F] = "TestMake"  # Make
        exif[0x0110] = "TestModel"  # Model
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def _png_bytes(size=(32, 32)) -> bytes:
    img = Image.new("RGBA", size, (1, 2, 3, 255))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _gif_bytes() -> bytes:
    img = Image.new("P", (16, 16))
    buf = BytesIO()
    img.save(buf, "GIF")
    return buf.getvalue()


def _make_shelf(client: TestClient) -> dict:
    room = _create_room(client)
    return _create_shelf(client, room["id"])


def _upload(client: TestClient, shelf_id: int, *, key: str,
            data: bytes | None = None, filename: str = "x.jpg",
            content_type: str = "image/jpeg",
            caption: str | None = None, is_primary: bool = False):
    form: dict[str, str] = {"idempotency_key": key,
                            "is_primary": "true" if is_primary else "false"}
    if caption is not None:
        form["caption"] = caption
    return client.post(
        f"/api/v1/storage/shelves/{shelf_id}/photos",
        files={"image": (filename, data if data is not None else _jpeg_bytes(),
                         content_type)},
        data=form)


def _owner_id(db_session: Session) -> int:
    return db_session.scalars(select(Member.id).where(Member.role == "owner")).first()


def _shelf_dir(shelf_id: int) -> Path:
    return settings.shelf_photos_dir / str(shelf_id)


# ── 上传与读取 ──


def test_upload_happy_path_and_shelf_detail(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    r = _upload(client, shelf["id"], key="p1", caption="正面照")
    assert r.status_code == 201, r.text
    photo = r.json()["data"]
    assert photo["shelf_id"] == shelf["id"]
    assert photo["mime_type"] == "image/jpeg"
    assert (photo["width"], photo["height"]) == (64, 64)
    assert photo["caption"] == "正面照"
    assert photo["is_primary"] is True  # 首张自动主图
    assert photo["version"] == 1

    # 文件落盘：路径不含原文件名
    row = db_session.get(ShelfPhoto, photo["id"])
    assert row.relative_path.startswith(f"shelf_photos/{shelf['id']}/")
    assert "x.jpg" not in row.relative_path
    assert (settings.data_dir / row.relative_path).is_file()

    # 书架详情含受授权的照片引用（§9）
    r = client.get(f"/api/v1/storage/shelves/{shelf['id']}")
    photos = r.json()["data"]["photos"]
    assert [p["id"] for p in photos] == [photo["id"]]

    # 清单分页
    r = client.get(f"/api/v1/storage/shelves/{shelf['id']}/photos")
    data = r.json()["data"]
    assert data["total"] == 1 and data["items"][0]["id"] == photo["id"]
    r = client.get("/api/v1/storage/shelves/999999/photos")
    assert r.status_code == 404


def test_content_endpoint_double_scope(client: TestClient, anon_client: TestClient,
                                       db_session: Session) -> None:
    shelf = _make_shelf(client)
    photo_id = _upload(client, shelf["id"], key="p1").json()["data"]["id"]
    url = f"/api/v1/storage/photos/{photo_id}/content"

    # 匿名 401
    assert anon_client.get(url).status_code == 401

    # 仅 locations:read → 403；仅 files:read → 403；双 scope → 200
    _, loc_only = _agent_token(client, ["locations:read"])
    r = client.get(url, headers={"Authorization": f"Bearer {loc_only}"})
    assert r.status_code == 403
    _, files_only = _agent_token(client, ["files:read"])
    r = client.get(url, headers={"Authorization": f"Bearer {files_only}"})
    assert r.status_code == 403
    _, both = _agent_token(client, ["locations:read", "files:read"])
    r = client.get(url, headers={"Authorization": f"Bearer {both}"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert Image.open(BytesIO(r.content)).size == (64, 64)

    # Member Web（member 能力集含两个 scope）→ 200
    with _member_web_client(db_session) as member_client:
        assert member_client.get(url).status_code == 200

    # 未知 id → 404（防枚举）
    assert client.get("/api/v1/storage/photos/999999/content").status_code == 404


def test_path_traversal_content_404(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    covers = settings.covers_dir
    covers.mkdir(parents=True, exist_ok=True)
    (covers / "c.jpg").write_bytes(_jpeg_bytes())
    # DB 直插越界路径（绕过服务端生成），内容端点必须拒读
    row = ShelfPhoto(shelf_id=shelf["id"], relative_path="../covers/c.jpg",
                     mime_type="image/jpeg", width=64, height=64)
    db_session.add(row)
    db_session.commit()
    assert client.get(f"/api/v1/storage/photos/{row.id}/content").status_code == 404


# ── 校验 ──


def test_invalid_image_422(client: TestClient) -> None:
    shelf = _make_shelf(client)
    r = _upload(client, shelf["id"], key="bad-1", data=b"not an image at all")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_IMAGE"
    assert not _shelf_dir(shelf["id"]).exists() or not list(_shelf_dir(shelf["id"]).iterdir())


def test_fake_extension_gif_422(client: TestClient) -> None:
    shelf = _make_shelf(client)
    r = _upload(client, shelf["id"], key="bad-2", data=_gif_bytes(), filename="fake.jpg")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_IMAGE"


def test_too_many_bytes_413(client: TestClient) -> None:
    shelf = _make_shelf(client)
    big = b"\xff\xd8" + b"x" * (10 * 1024 * 1024)  # >10MiB
    r = _upload(client, shelf["id"], key="big-bytes", data=big)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "PHOTO_TOO_LARGE"


def test_too_many_pixels_413(client: TestClient) -> None:
    shelf = _make_shelf(client)
    # 4608×4352 = 20,054,016 > 2000 万；纯色图 JPEG 字节很小，只触发像素上限
    big = _jpeg_bytes(size=(4608, 4352))
    r = _upload(client, shelf["id"], key="big-pixels", data=big)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "PHOTO_TOO_LARGE"


def test_exif_stripped(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    raw = _jpeg_bytes(with_exif=True)
    assert Image.open(BytesIO(raw)).getexif()  # 源图确实带 EXIF
    photo_id = _upload(client, shelf["id"], key="exif", data=raw).json()["data"]["id"]

    row = db_session.get(ShelfPhoto, photo_id)
    stored = (settings.data_dir / row.relative_path).read_bytes()
    assert not Image.open(BytesIO(stored)).getexif()  # 落盘文件无 EXIF
    r = client.get(f"/api/v1/storage/photos/{photo_id}/content")
    assert not Image.open(BytesIO(r.content)).getexif()  # 服务端下发同样无 EXIF


def test_photo_limit_per_shelf(client: TestClient) -> None:
    shelf = _make_shelf(client)
    for i in range(5):
        r = _upload(client, shelf["id"], key=f"p{i}")
        assert r.status_code == 201, r.text
    r = _upload(client, shelf["id"], key="p5")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "PHOTO_LIMIT_EXCEEDED"
    # 拒收后架目录无残留（上传失败仅清理本次暂存文件）
    leftovers = [p for p in _shelf_dir(shelf["id"]).iterdir()
                 if p.name.startswith("tmp-")]
    assert leftovers == []
    assert len(list(_shelf_dir(shelf["id"]).iterdir())) == 5


def test_png_webp_accepted(client: TestClient) -> None:
    shelf = _make_shelf(client)
    r = _upload(client, shelf["id"], key="png", data=_png_bytes(),
                filename="x.png", content_type="image/png")
    assert r.status_code == 201
    assert r.json()["data"]["mime_type"] == "image/png"
    img = Image.new("RGB", (24, 24), (9, 9, 9))
    buf = BytesIO()
    img.save(buf, "WEBP")
    r = _upload(client, shelf["id"], key="webp", data=buf.getvalue(),
                filename="x.webp", content_type="image/webp")
    assert r.status_code == 201
    assert r.json()["data"]["mime_type"] == "image/webp"


# ── 改标注 / 主图 ──


def test_update_caption_and_version_conflict(client: TestClient,
                                             db_session: Session) -> None:
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="p1").json()["data"]
    r = client.patch(f"/api/v1/storage/photos/{photo['id']}",
                     json={"version": 1, "caption": "侧面"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["caption"] == "侧面"
    assert r.json()["data"]["version"] == 2

    r = client.patch(f"/api/v1/storage/photos/{photo['id']}",
                     json={"version": 1, "caption": "过期版本"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"

    actions = db_session.scalars(
        select(OperationLog.action)
        .where(OperationLog.action == "storage.photo.update")).all()
    assert len(actions) == 1


def test_primary_switch_concurrent(client: TestClient, db_session: Session) -> None:
    """真实线程并发轮流设主图：最终架内恰好一张主图，且无唯一索引冲突泄漏。"""
    shelf = _make_shelf(client)
    p1 = _upload(client, shelf["id"], key="p1").json()["data"]["id"]
    p2 = _upload(client, shelf["id"], key="p2").json()["data"]["id"]
    owner_id = _owner_id(db_session)
    errors: list[BaseException | str] = []

    def worker(photo_id: int) -> None:
        session = db_module.SessionLocal()
        try:
            for _ in range(30):
                version = session.get(ShelfPhoto, photo_id).version
                try:
                    storage_photos.update_photo(
                        session, photo_id,
                        PhotoUpdate(version=version, is_primary=True),
                        operator_member_id=owner_id)
                    return
                except PlacementChanged:
                    session.rollback()
                    continue
            errors.append("retry exhausted")
        except Exception as exc:  # noqa: BLE001 - 汇聚到主线程断言
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(pid,))
               for pid in (p1, p2) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors

    db_session.expire_all()
    primaries = db_session.scalars(
        select(ShelfPhoto).where(ShelfPhoto.shelf_id == shelf["id"],
                                 ShelfPhoto.is_primary.is_(True))).all()
    assert len(primaries) == 1

    # 顺序设主图：旧主图被同事务清掉
    other = p2 if primaries[0].id == p1 else p1
    cur = db_session.get(ShelfPhoto, other)
    r = client.patch(f"/api/v1/storage/photos/{other}",
                     json={"version": cur.version, "is_primary": True})
    assert r.status_code == 200
    db_session.expire_all()
    primaries = db_session.scalars(
        select(ShelfPhoto).where(ShelfPhoto.shelf_id == shelf["id"],
                                 ShelfPhoto.is_primary.is_(True))).all()
    assert [p.id for p in primaries] == [other]


# ── 删除与回收 ──


def test_delete_removes_file_and_gc_done(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="p1").json()["data"]
    row = db_session.get(ShelfPhoto, photo["id"])
    file_path = settings.data_dir / row.relative_path
    assert file_path.is_file()

    r = client.delete(f"/api/v1/storage/photos/{photo['id']}", params={"version": 99})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PLACEMENT_CHANGED"

    r = client.delete(f"/api/v1/storage/photos/{photo['id']}", params={"version": 1})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"photo_id": photo["id"], "deleted": True}
    assert db_session.get(ShelfPhoto, photo["id"]) is None
    assert not file_path.exists()  # commit 后即时回收已删文件

    jobs = db_session.scalars(select(LocationFileGcJob)).all()
    assert len(jobs) == 1 and jobs[0].status == "done"
    assert jobs[0].relative_path == row.relative_path
    actions = db_session.scalars(
        select(OperationLog.action)
        .where(OperationLog.action == "storage.photo.delete")).all()
    assert len(actions) == 1


def test_gc_interrupted_recovery(client: TestClient, db_session: Session) -> None:
    """模拟进程中断：DB 行与文件都在、GC 任务 pending → 受控清理删文件置 done。"""
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="p1").json()["data"]
    row = db_session.get(ShelfPhoto, photo["id"])
    rel = row.relative_path
    file_path = settings.data_dir / rel
    # 模拟中断现场：DB 行没了（删除已提交）、文件还在、任务 pending 未执行
    db_session.delete(row)
    db_session.query(LocationFileGcJob).delete()
    db_session.add(LocationFileGcJob(relative_path=rel, reason="photo_deleted"))
    db_session.commit()
    assert file_path.is_file()

    summary = storage_photos.process_gc_jobs(db_session)
    assert summary["done"] == 1
    assert not file_path.exists()
    assert db_session.scalars(select(LocationFileGcJob)).one().status == "done"


def test_gc_containment_refuses_outside(client: TestClient, db_session: Session) -> None:
    """回收目标越出 shelf_photos 目录：置 failed，绝不动文件。"""
    _make_shelf(client)
    covers = settings.covers_dir
    covers.mkdir(parents=True, exist_ok=True)
    target = covers / "keep.jpg"
    target.write_bytes(_jpeg_bytes())
    db_session.add(LocationFileGcJob(relative_path="../covers/keep.jpg",
                                     reason="photo_deleted"))
    db_session.add(LocationFileGcJob(relative_path="covers/keep.jpg",
                                     reason="photo_deleted"))
    db_session.commit()

    summary = storage_photos.process_gc_jobs(db_session)
    assert summary["failed"] == 2 and summary["done"] == 0
    assert target.is_file()
    for job in db_session.scalars(select(LocationFileGcJob)).all():
        assert job.status == "failed"
        assert "越出" in job.last_error


def test_gc_still_referenced_not_deleted(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="p1").json()["data"]
    row = db_session.get(ShelfPhoto, photo["id"])
    file_path = settings.data_dir / row.relative_path
    db_session.add(LocationFileGcJob(relative_path=row.relative_path,
                                     reason="photo_deleted"))
    db_session.commit()

    summary = storage_photos.process_gc_jobs(db_session)
    assert summary["failed"] == 1
    assert file_path.is_file()  # 仍被引用，文件不动
    job = db_session.scalars(select(LocationFileGcJob)).one()
    assert job.status == "failed" and "引用" in job.last_error


def test_gc_missing_file_is_done(db_session: Session) -> None:
    db_session.add(LocationFileGcJob(
        relative_path="shelf_photos/1/never-existed.jpg", reason="photo_deleted"))
    db_session.commit()
    summary = storage_photos.process_gc_jobs(db_session)
    assert summary["done"] == 1  # 删除不存在文件视为完成
    assert db_session.scalars(select(LocationFileGcJob)).one().status == "done"


def test_orphan_audit_readonly(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="p1").json()["data"]
    row = db_session.get(ShelfPhoto, photo["id"])
    referenced = settings.data_dir / row.relative_path
    old = referenced.stat().st_mtime - 48 * 3600
    os.utime(referenced, (old, old))  # 被引用的旧文件：不列

    orphan_dir = _shelf_dir(shelf["id"])
    young = orphan_dir / "young-orphan.jpg"
    young.write_bytes(_jpeg_bytes())  # <24h：不列
    aged = orphan_dir / "aged-orphan.jpg"
    aged.write_bytes(_jpeg_bytes())
    aged_ts = aged.stat().st_mtime - 48 * 3600
    os.utime(aged, (aged_ts, aged_ts))  # >24h 无引用：列
    covers = settings.covers_dir
    covers.mkdir(parents=True, exist_ok=True)
    cover_orphan = covers / "cover-orphan.jpg"
    cover_orphan.write_bytes(_jpeg_bytes())
    cover_ts = cover_orphan.stat().st_mtime - 48 * 3600
    os.utime(cover_orphan, (cover_ts, cover_ts))  # covers 不在扫描范围

    orphans = storage_photos.orphan_audit(db_session)
    assert [o["relative_path"] for o in orphans] == [
        f"shelf_photos/{shelf['id']}/aged-orphan.jpg"]
    assert orphan_dir.joinpath("aged-orphan.jpg").is_file()  # 只读，不删


# ── 幂等与权限 ──


def test_idempotent_replay_upload(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    data = _jpeg_bytes()
    r1 = _upload(client, shelf["id"], key="idem-1", data=data, caption="同载荷")
    r2 = _upload(client, shelf["id"], key="idem-1", data=data, caption="同载荷")
    assert r1.status_code == 201 and r2.status_code == 201
    assert r2.json()["data"]["id"] == r1.json()["data"]["id"]
    photos = db_session.scalars(
        select(ShelfPhoto).where(ShelfPhoto.shelf_id == shelf["id"])).all()
    assert len(photos) == 1  # 重放不新建
    files = list(_shelf_dir(shelf["id"]).iterdir())
    assert len(files) == 1  # 重放仅清理本次暂存文件，不残留

    r = _upload(client, shelf["id"], key="idem-1", data=data, caption="换载荷")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_delete_same_key_different_version_conflicts(client: TestClient) -> None:
    """BUG-316：删除的幂等摘要须含期望版本（M0 契约"同 key 不同摘要 409"）。

    旧版摘要不含 version：同 key 不同期望版本的迟到删除会重放旧回执误报
    成功，而非提示操作意图已变化。
    """
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="delv-1").json()["data"]

    r1 = client.delete(f"/api/v1/storage/photos/{photo['id']}",
                       params={"version": photo["version"],
                               "idempotency_key": "delv-key"})
    assert r1.status_code == 200, r1.text
    # 同 key 同版本重放：仍返回原回执
    r2 = client.delete(f"/api/v1/storage/photos/{photo['id']}",
                       params={"version": photo["version"],
                               "idempotency_key": "delv-key"})
    assert r2.status_code == 200, r2.text
    # 同 key 不同期望版本：不同操作意图，应 409 而非重放旧回执
    r3 = client.delete(f"/api/v1/storage/photos/{photo['id']}",
                       params={"version": photo["version"] + 1,
                               "idempotency_key": "delv-key"})
    assert r3.status_code == 409, r3.text
    assert r3.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_update_same_key_different_version_conflicts(client: TestClient) -> None:
    """BUG-316：更新的幂等摘要同样含期望版本（与 update_room 等口径一致）。

    同 key 不同 version 的重试是不同操作意图，应 409 而非重放旧回执。
    """
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="updv-1").json()["data"]

    r1 = client.patch(f"/api/v1/storage/photos/{photo['id']}",
                      json={"version": photo["version"], "caption": "第一版",
                            "idempotency_key": "updv-key"})
    assert r1.status_code == 200, r1.text
    # 同 key 同载荷重放：返回原回执
    r2 = client.patch(f"/api/v1/storage/photos/{photo['id']}",
                      json={"version": photo["version"], "caption": "第一版",
                            "idempotency_key": "updv-key"})
    assert r2.status_code == 200, r2.text
    # 同 key 不同期望版本：409
    r3 = client.patch(f"/api/v1/storage/photos/{photo['id']}",
                      json={"version": photo["version"] + 1, "caption": "第一版",
                            "idempotency_key": "updv-key"})
    assert r3.status_code == 409, r3.text
    assert r3.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_upload_audit_recorded(client: TestClient, db_session: Session) -> None:
    shelf = _make_shelf(client)
    _upload(client, shelf["id"], key="audit-p1")
    actions = db_session.scalars(
        select(OperationLog.action)
        .where(OperationLog.action == "storage.photo.upload")).all()
    assert len(actions) == 1


def test_write_permission_matrix(client: TestClient, anon_client: TestClient,
                                 db_session: Session) -> None:
    shelf = _make_shelf(client)
    photo = _upload(client, shelf["id"], key="perm-p1").json()["data"]
    upload_files = {"image": ("x.jpg", _jpeg_bytes(), "image/jpeg")}
    upload_data = {"idempotency_key": "m-up", "is_primary": "false"}

    # 匿名：写 401、读 401
    assert anon_client.post(f"/api/v1/storage/shelves/{shelf['id']}/photos",
                            files=upload_files, data=upload_data).status_code == 401
    assert anon_client.get(
        f"/api/v1/storage/shelves/{shelf['id']}/photos").status_code == 401

    # Member Web：写 403
    with _member_web_client(db_session) as member_client:
        r = member_client.post(f"/api/v1/storage/shelves/{shelf['id']}/photos",
                               files=upload_files, data=upload_data,
                               headers={"Origin": "http://127.0.0.1"})
        assert r.status_code == 403
        r = member_client.patch(f"/api/v1/storage/photos/{photo['id']}",
                                json={"version": 1, "caption": "越权"},
                                headers={"Origin": "http://127.0.0.1"})
        assert r.status_code == 403
        r = member_client.delete(f"/api/v1/storage/photos/{photo['id']}",
                                 params={"version": 1},
                                 headers={"Origin": "http://127.0.0.1"})
        assert r.status_code == 403

    # Agent Token（无 Cookie）：写 401，即便带 locations:read
    _, token = _agent_token(client, ["locations:read", "files:read"])
    from app.main import app
    with TestClient(app) as token_only:
        r = token_only.post(f"/api/v1/storage/shelves/{shelf['id']}/photos",
                            files=upload_files, data=upload_data,
                            headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        r = token_only.delete(f"/api/v1/storage/photos/{photo['id']}",
                              params={"version": 1},
                              headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


def test_upload_to_archived_shelf_rejected(client: TestClient) -> None:
    shelf = _make_shelf(client)
    r = client.patch(f"/api/v1/storage/shelves/{shelf['id']}",
                     json={"version": 1, "archived": True})
    assert r.status_code == 200
    r = _upload(client, shelf["id"], key="arch-p1")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LOCATION_ARCHIVED"


# ── 转正恢复与照片 ID 不复用（补充复核缺陷修复回归） ──


def _rel_of(db_session: Session, photo_id: int) -> str:
    return db_session.get(ShelfPhoto, photo_id).relative_path


def test_upload_replay_promotes_interrupted_staged(client: TestClient, db_session: Session) -> None:
    """commit 后、转正前中断：同键重试先补转正原暂存文件，不留不可读照片。"""
    shelf = _make_shelf(client)
    r = _upload(client, shelf["id"], key="promo-1", caption="中断转正")
    assert r.status_code == 201, r.text
    photo = r.json()["data"]
    final = settings.data_dir / _rel_of(db_session, photo["id"])
    assert final.is_file()
    # 模拟中断窗口：事务已提交、replace 未执行（final 退回 .staged）
    staged = Path(f"{final}.staged")
    final.rename(staged)

    r2 = _upload(client, shelf["id"], key="promo-1", caption="中断转正")
    assert r2.status_code == 201, r2.text
    assert r2.json()["data"]["id"] == photo["id"]
    assert final.is_file(), "同键重试应补转正中断的暂存文件"
    assert not staged.exists()


def test_promote_staged_uploads_recovers_and_cleans(client: TestClient, db_session: Session) -> None:
    """启动恢复：被引用且 final 缺失的 .staged 转正；无引用残留清理；
    orphan_audit 不把 .staged 当孤儿。"""
    shelf = _make_shelf(client)
    r = _upload(client, shelf["id"], key="promo-2")
    assert r.status_code == 201
    photo_id = r.json()["data"]["id"]
    final = settings.data_dir / _rel_of(db_session, photo_id)
    staged = Path(f"{final}.staged")
    final.rename(staged)
    # 无引用且已超过宽限的残留暂存（事务失败/崩溃遗留）才清理。
    # 新鲜无引用暂存可能是其他实例尚未提交的上传，不得立刻删（BUG-301）。
    orphan_staged = settings.shelf_photos_dir / str(shelf["id"]) / "deadbeef.jpg.staged"
    orphan_staged.write_bytes(b"x")
    stale = time.time() - storage_photos.STAGED_ORPHAN_GRACE_SECONDS - 60
    os.utime(orphan_staged, (stale, stale))

    summary = storage_photos.promote_staged_uploads(db_session)

    assert summary["promoted"] == 1
    assert summary["removed"] == 1
    assert final.is_file()
    assert not staged.exists() and not orphan_staged.exists()
    assert client.get(f"/api/v1/storage/photos/{photo_id}/content").status_code == 200

    staged.write_bytes(b"y")
    orphans = storage_photos.orphan_audit(db_session)
    assert all(not item["relative_path"].endswith(".staged") for item in orphans)


def test_upload_replay_content_truly_missing_keeps_receipt(client: TestClient, db_session: Session) -> None:
    """final 与 .staged 均缺失（文件真丢）：重试仍返回原回执且不崩溃。"""
    shelf = _make_shelf(client)
    r = _upload(client, shelf["id"], key="promo-3")
    assert r.status_code == 201
    photo_id = r.json()["data"]["id"]
    (settings.data_dir / _rel_of(db_session, photo_id)).unlink()

    r2 = _upload(client, shelf["id"], key="promo-3")
    assert r2.status_code == 201
    assert r2.json()["data"]["id"] == photo_id


def test_photo_id_not_reused_after_delete(client: TestClient, db_session: Session) -> None:
    """SQLite 默认 rowid 复用已删最大 ID 且新照片 version 重置为 1：迟到的
    旧删除请求可误删新照片。AUTOINCREMENT 后 ID 单调不复用，旧请求只落 404。"""
    shelf = _make_shelf(client)
    r1 = _upload(client, shelf["id"], key="reuse-1")
    assert r1.status_code == 201
    old_id = r1.json()["data"]["id"]
    old_version = r1.json()["data"]["version"]
    rd = client.delete(f"/api/v1/storage/photos/{old_id}",
                       params={"version": old_version, "idempotency_key": "reuse-del"})
    assert rd.status_code == 200

    r2 = _upload(client, shelf["id"], key="reuse-2")
    assert r2.status_code == 201
    new_id = r2.json()["data"]["id"]
    assert new_id != old_id, "新照片不得复用已删除照片的 ID"

    # 迟到的旧删除请求（旧 ID、version=1）：不得影响新照片
    rlate = client.delete(f"/api/v1/storage/photos/{old_id}",
                          params={"version": 1, "idempotency_key": "reuse-del-late"})
    assert rlate.status_code == 404
    photos = client.get(f"/api/v1/storage/shelves/{shelf['id']}").json()["data"]["photos"]
    assert [p["id"] for p in photos] == [new_id]


def test_promote_keeps_fresh_unreferenced_staged(client: TestClient, db_session: Session) -> None:
    """尚无照片引用的新鲜暂存是其他实例可能尚未提交的上传，启动恢复不得删除。"""
    shelf = _make_shelf(client)
    fresh = settings.shelf_photos_dir / str(shelf["id"]) / "inflight.jpg.staged"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"still-uploading")

    summary = storage_photos.promote_staged_uploads(db_session)

    assert fresh.is_file()
    assert summary["kept"] == 1
    assert summary["removed"] == 0


def test_concurrent_promote_same_staged_both_succeed(db_session: Session, monkeypatch) -> None:
    """两个同键恢复同时转正：后到的 replace 失败后重验 final，两边都算成功，不抛异常。"""
    rel = "shelf_photos/9/race.jpg"
    final = settings.data_dir / rel
    final.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(f"{final}.staged")
    staged.write_bytes(b"jpeg-bytes")
    barrier = threading.Barrier(2)
    original_is_file = Path.is_file

    def staged_is_file(path: Path) -> bool:
        result = original_is_file(path)
        if path == staged and result:
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(Path, "is_file", staged_is_file)
    outcomes: list[object] = []

    def run() -> None:
        try:
            outcomes.append(storage_photos._promote_staged(rel))
        except Exception as exc:  # noqa: BLE001 — 测试要捕获意外抛出
            outcomes.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert outcomes == [True, True]
    assert final.is_file()
    assert not staged.exists()
