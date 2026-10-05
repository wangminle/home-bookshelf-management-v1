"""BUG-284～287 回归：批量入库工作流并发与重建缺陷修复。

覆盖：
- BUG-284：并发上传同一 photo_id——后提交方撞唯一约束 uq_intake_photo_pid
  得到 409 语义业务错误，且失败清理只删除本批自己写入的文件（落盘路径含
  随机后缀），先提交方的数据库记录与文件完好；
- BUG-285：候选确认后重新识别/重建——已确认/已执行候选引用的照片不参与
  新分组，同一照片不得同时属于保留候选与新待核对候选（否则确认执行会把
  同一照片建成两本不同的书）；
- BUG-286：识别全局能力故障（模型禁用/未配置/鉴权失败）如实报错且不触发
  候选重建，人工编辑过的候选（version > 1）原样保留；单张局部失败重建时
  同样不得覆盖人工编辑过的候选；
- BUG-287：已执行候选不可拆分（与"不可编辑"对齐）——拆分会使已完成命令
  永久失格，任务被重算为 failed 且无法恢复。
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Book, IntakeCandidate, IntakePhoto
from app.services import intake_workflow as wf
from app.services.intake_workflow import WorkflowError
from app.services.vision import VisionCandidate, VisionField, VisionServiceResult


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _png_bytes(color=(1, 2, 3)) -> bytes:
    """真实可解码 PNG：候选执行时会走条码识别，假字节会被判图片损坏。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


def _mk_item_with_photos(db_engine, photos) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="回归批次")
        wf.add_photos(s, item.id, photos)
        return item.id


def _candidates_of(s, item_id) -> list[IntakeCandidate]:
    return s.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == item_id)).fetchall()


def _vision_ok(**kw) -> VisionServiceResult:
    fields = {k: VisionField(value=v, readable=True)
              for k, v in ({"title": kw.get("title"), "authors": kw.get("authors"),
                            "isbn": kw.get("isbn")}).items() if v}
    candidate = VisionCandidate(
        title=kw.get("title"), authors=kw.get("authors") or [],
        isbn=kw.get("isbn"), fields=fields, confidence=kw.get("confidence", 0.9))
    return VisionServiceResult(ok=True, candidate=candidate, message="识别完成")


def _vision_fail(error_code: str, message: str = "失败") -> VisionServiceResult:
    return VisionServiceResult(ok=False, error_code=error_code, message=message)


# ── BUG-284：并发上传同一 photo_id 的文件安全 ──

def test_concurrent_photo_id_upload_failure_keeps_first_file(db_engine, monkeypatch):
    """第二请求的存在性检查与第一请求的提交并发交错（检查结果过期），
    落盘后撞唯一约束：第二请求得到 409 语义业务错误、只清理自己写入的文件；
    第一请求的记录与文件完好。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        s.commit()
        item_id = item.id
    # 第一请求：正常上传成功
    with SessionLocal() as s:
        saved = wf.add_photos(s, item_id, [
            {"photo_id": "p0001", "filename": "a.jpg", "content": b"AAA", "role": "cover"}])
        first_path = wf.photo_abs_path(saved[0])
    assert first_path.is_file()

    # 第二请求：存在性检查返回空集（模拟检查时第一请求尚未提交的并发交错），
    # 随后的真实提交撞 uq_intake_photo_pid
    with SessionLocal() as s:
        real_scalars = s.scalars
        state = {"stale_read_done": False}

        def stale_scalars(*args, **kwargs):
            if not state["stale_read_done"]:
                state["stale_read_done"] = True
                return iter(())  # 过期读：检查时刻对方尚未提交
            return real_scalars(*args, **kwargs)

        monkeypatch.setattr(s, "scalars", stale_scalars)
        with pytest.raises(WorkflowError, match="photo_id 已存在"):
            wf.add_photos(s, item_id, [
                {"photo_id": "p0001", "filename": "b.jpg", "content": b"BBB", "role": "cover"}])

    # 第一请求的文件与记录完好；第二请求的文件已被自行清理，无残留
    assert first_path.is_file()
    assert first_path.read_bytes() == b"AAA"
    assert [p for p in first_path.parent.iterdir() if p != first_path] == []
    with SessionLocal() as s:
        photos = s.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == item_id)).fetchall()
        assert len(photos) == 1
        assert photos[0].sha256 == hashlib.sha256(b"AAA").hexdigest()
        # 读侧 photo_abs_path 仍正常（image 端点兼容性）
        assert wf.photo_abs_path(photos[0]) == first_path


def test_upload_disk_paths_unique_per_attempt(db_engine, monkeypatch):
    """落盘路径含随机后缀：并发撞车的两次尝试不会写同一路径。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        s.commit()
        item_id = item.id
    with SessionLocal() as s:
        saved = wf.add_photos(s, item_id, [
            {"photo_id": "p0001", "filename": "a.jpg", "content": b"AAA", "role": "cover"}])
        first_path = wf.photo_abs_path(saved[0])
    with SessionLocal() as s:
        real_scalars = s.scalars
        state = {"stale_read_done": False}

        def stale_scalars(*args, **kwargs):
            if not state["stale_read_done"]:
                state["stale_read_done"] = True
                return iter(())
            return real_scalars(*args, **kwargs)

        monkeypatch.setattr(s, "scalars", stale_scalars)
        written: list[Path] = []
        real_write = Path.write_bytes

        def spy_write(self, data):
            written.append(self)
            return real_write(self, data)

        monkeypatch.setattr(Path, "write_bytes", spy_write)
        with pytest.raises(WorkflowError, match="photo_id 已存在"):
            wf.add_photos(s, item_id, [
                {"photo_id": "p0001", "filename": "b.jpg", "content": b"BBB", "role": "cover"}])
        assert len(written) == 1 and written[0] != first_path  # 独立路径，未共享


def test_duplicate_photo_id_via_api_is_409(client):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", b"a", "image/png"))],
                    data={"photo_ids": "p0001"}, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("b.png", b"b", "image/png"))],
                    data={"photo_ids": "p0001"}, headers=headers)
    assert r.status_code == 409, r.text
    assert "已存在" in r.text


# ── BUG-285：重建候选排除已确认/已执行候选的照片 ──

def test_rebuild_excludes_photos_of_confirmed_and_executed_candidates(db_engine):
    """确认候选甲后重跑识别（甲的照片得到不同书名且无 ISBN）：
    甲的照片不得进入新候选；确认执行新候选后同一照片只建一本书。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
        {"photo_id": "p0002", "filename": "b.png", "content": _png_bytes((2, 2, 2)),
         "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="甲书", confidence=0.9),
            "p0002": wf.RecognitionInput(title="乙书", confidence=0.8),
        })
        cands = _candidates_of(s, item_id)
        first = next(c for c in cands if c.title == "甲书")
        wf.confirm_candidates(s, item_id, [first.id], decided_by_member_id=1)

        # 重新识别：甲的照片被识别成完全不同的书名（且无 ISBN），乙的照片不变
        cands = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="丙书（新识别）", confidence=0.9),
            "p0002": wf.RecognitionInput(title="乙书", confidence=0.8),
        })
        # 新候选只来自未被保留候选认领的 p0002
        assert len(cands) == 1
        assert json.loads(cands[0].photo_ids) == ["p0002"]
        assert cands[0].title == "乙书"
        # 甲的已确认候选原样保留、仍独占 p0001
        first = s.get(IntakeCandidate, first.id)
        assert first.status == "confirmed"
        assert json.loads(first.photo_ids) == ["p0001"]

        # 确认并执行新候选：同一照片不会被建成第二本书
        wf.confirm_candidates(s, item_id, [cands[0].id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        books = s.scalars(select(Book)).fetchall()
        assert sorted(b.title for b in books) == ["乙书", "甲书"]

        # 全部候选 executed 后再重建：无新候选产生
        assert wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="丙书（新识别）"),
        }) == []


# ── BUG-286：识别全局故障不重建；局部失败不覆盖人工候选 ──

def test_recognize_global_failure_keeps_existing_candidates(db_engine, monkeypatch):
    """模型禁用（全局能力故障）：recognize 返回错误且不触发候选重建，
    人工编辑过的候选原样保留。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="错误识别", confidence=0.3)})
        cand = _candidates_of(s, item_id)[0]
        # 用户已保存的人工书名（编辑 → version 2）
        wf.update_candidate(s, cand.id, title="人工书名", authors=["人工作者"])
        cand_id = cand.id
    monkeypatch.setattr(
        "app.services.vision.recognize_cover_fields",
        lambda db, path, **kw: _vision_fail("disabled", "视觉识别服务未启用"))
    with SessionLocal() as s:
        with pytest.raises(WorkflowError, match="视觉识别不可用"):
            wf.recognize_photos(s, item_id)
        cand = s.get(IntakeCandidate, cand_id)
        assert cand.status == "pending_review"
        assert cand.version == 2
        assert cand.title == "人工书名"
        assert json.loads(cand.authors) == ["人工作者"]
        assert len(_candidates_of(s, item_id)) == 1  # 无候选被删除/新增


def test_recognize_global_failure_via_api(db_engine, client, monkeypatch):
    """API 层：全局故障映射 4xx（409），既有候选保留，接口不再假成功。"""
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", _png_bytes(), "image/png"))],
                    headers=headers)
    assert r.status_code == 200, r.text
    monkeypatch.setattr(
        "app.services.vision.recognize_cover_fields",
        lambda db, path, **kw: _vision_fail("not_configured", "配置不完整"))
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/recognize",
                    headers=headers)
    assert r.status_code == 409, r.text
    assert "视觉识别不可用" in r.text
    r = client.get(f"/api/v1/intake-workflow/work-items/{item_id}", headers=headers)
    data = r.json()["data"]
    assert len(data["photos"]) == 1
    assert data["candidates"] == []  # 全局故障未触发候选重建


def test_recognize_partial_failure_preserves_edited_candidate(db_engine, monkeypatch):
    """单张局部失败（非全局故障）：仍重建候选，但人工编辑过的候选
    （version > 1）不被覆盖，其照片也不进入新分组。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
        {"photo_id": "p0002", "filename": "b.png", "content": _png_bytes((2, 2, 2)),
         "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="错误识别", confidence=0.3),
            "p0002": wf.RecognitionInput(title="乙书", confidence=0.8),
        })
        edited = next(c for c in _candidates_of(s, item_id) if c.title == "错误识别")
        wf.update_candidate(s, edited.id, title="人工书名", authors=["人工作者"])
        edited_id = edited.id

    def fake_recognize(db, path: Path, **kw):
        if path.name.startswith("p0001"):
            return _vision_ok(title="丙书（重识别）", confidence=0.95)
        return _vision_fail("image_unreadable", "无法读取图片")

    monkeypatch.setattr("app.services.vision.recognize_cover_fields", fake_recognize)
    with SessionLocal() as s:
        summary = wf.recognize_photos(s, item_id)
        assert summary["photos_total"] == 2
        assert [f["photo_id"] for f in summary["failures"]] == ["p0002"]
        # 人工编辑过的候选原样保留（未被删除/取代/改写字段）
        edited = s.get(IntakeCandidate, edited_id)
        assert edited.status == "pending_review"
        assert edited.version == 2
        assert edited.title == "人工书名"
        assert json.loads(edited.photo_ids) == ["p0001"]
        # 新候选只来自未被认领的照片 p0002（p0001 的"丙书"结果不另建候选）
        others = [c for c in _candidates_of(s, item_id) if c.id != edited_id]
        assert len(others) == 1
        assert json.loads(others[0].photo_ids) == ["p0002"]


# ── BUG-287：已执行候选不可拆分 ──

def test_split_executed_candidate_rejected(db_engine):
    """候选执行完成后拆分：与 update_candidate 对齐显式拒绝，
    任务状态与候选版本/照片关联不被破坏。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
        {"photo_id": "p0002", "filename": "b.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", authors=["刘慈欣"], confidence=0.9),
            "p0002": wf.RecognitionInput(title="三体", authors=["刘慈欣"], confidence=0.8),
        })
        cand = _candidates_of(s, item_id)[0]
        assert json.loads(cand.photo_ids) == ["p0001", "p0002"]
        cand_id = cand.id
        wf.confirm_candidates(s, item_id, [cand_id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"

    with SessionLocal() as s:
        with pytest.raises(WorkflowError, match="已执行的候选不可拆分"):
            wf.split_candidate(s, cand_id, photo_ids_to_new=["p0002"])
        # 拆分被拒后一切如初：候选版本/照片关联未变，任务仍 completed
        cand = s.get(IntakeCandidate, cand_id)
        assert cand.status == "executed"
        assert cand.version == 1
        assert json.loads(cand.photo_ids) == ["p0001", "p0002"]
        assert wf.get_work_item(s, item_id).status == "completed"
        assert s.scalar(select(func.count()).select_from(IntakeCandidate)) == 1


def test_split_executed_candidate_via_api(db_engine, client):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", _png_bytes(), "image/png")),
                           ("files", ("b.png", _png_bytes(), "image/png"))],
                    headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/candidates",
                    json={"recognized": {
                        "p0001": {"title": "三体", "authors": ["刘慈欣"], "confidence": 0.9},
                        "p0002": {"title": "三体", "authors": ["刘慈欣"], "confidence": 0.8}}},
                    headers=headers)
    assert r.status_code == 200, r.text
    cand_id = r.json()["data"]["candidates"][0]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/confirm",
                    json={"candidate_ids": [cand_id]}, headers=headers)
    assert r.status_code == 200, r.text
    with patch("app.services.intake.fetch_metadata", return_value=None):
        r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/execute",
                        headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "completed"

    r = client.post(f"/api/v1/intake-workflow/candidates/{cand_id}/split",
                    json={"photo_ids": ["p0002"]}, headers=headers)
    assert r.status_code == 409, r.text
    assert "不可拆分" in r.text
    r = client.get(f"/api/v1/intake-workflow/work-items/{item_id}", headers=headers)
    assert r.json()["data"]["status"] == "completed"  # 任务状态未被破坏
