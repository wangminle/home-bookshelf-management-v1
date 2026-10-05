"""BUG-285/286/289 回归：批量入库工作流复核残口修复。

覆盖：
- BUG-289：租约未过期的 executing 命令被执行器按设计跳过后，任务汇总
  （execute_work_item 与 _recompute_work_item_status）保持 executing 而非
  failed——否则前端恢复执行入口消失、命令又无重试按钮，租约过期后无从恢复；
  租约过期后再次执行走恢复路径完成任务（completed）；
- BUG-286：拆分出的 v1 候选继承了人工字段（evidence source=user），以及
  手工构建接口直接保存的 source=user v1 候选，都不被识别重建清空；纯模型
  来源的 v1 候选仍被新识别结果取代（保护不得过宽）；
- BUG-285：确认持有任务写锁时重建等待，确认提交后重建读取照片归属且不创建
  重复候选；确认侧同样校验照片未被其他已确认/已执行候选占用；正常串行
  重建（确认后再重建）语义不受影响。
"""
from __future__ import annotations

import io
import json
import threading
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import Book, IntakeCandidate, IntakeCommandExecution
from app.services import intake_workflow as wf
from app.services.intake_workflow import WorkflowError
from app.services.vision import VisionCandidate, VisionServiceResult


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _png_bytes(color=(1, 2, 3)) -> bytes:
    """真实可解码 PNG：执行链路会读图片内容。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


def _mk_item_with_photos(db_engine, photos) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="复核残口回归")
        wf.add_photos(s, item.id, photos)
        return item.id


def _candidates_of(s, item_id) -> list[IntakeCandidate]:
    return s.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == item_id)).fetchall()


# ── BUG-289：租约未过期时任务保持 executing，过期后可恢复 ──

def test_unexpired_lease_keeps_task_executing(db_engine):
    """任务与命令均 executing、持久化租约未过期：执行器跳过该命令（正确），
    汇总不得因完成数为 0 判成 failed——任务保持 executing，恢复入口不消失。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        cand = IntakeCandidate(work_item_id=item.id, title="恢复探针",
                               status="pending_review", photo_ids="[]")
        s.add(cand)
        s.commit()
        command = wf.confirm_candidates(s, item.id, [cand.id],
                                        decided_by_member_id=None)[0]
        command.status = "executing"
        command.lease_owner = "crashed-worker"
        command.lease_expires_at = wf._now() + timedelta(seconds=30)
        item.status = "executing"
        s.commit()
        item_id, command_id = item.id, command.id

        result = wf.execute_work_item(s, item_id)
        assert result["status"] == "executing"  # 不得判成 failed
        command = s.scalars(select(IntakeCommandExecution)).first()
        assert command.status == "executing"
        assert wf._as_aware(command.lease_expires_at) > wf._now()

        # 同一口径的直接重算也不得翻成 failed
        assert wf._recompute_work_item_status(s, item_id) == "executing"
        assert wf.get_work_item(s, item_id).status == "executing"


def test_expired_lease_recovers_to_completed(db_engine):
    """租约过期后再次执行：恢复路径（查重/重新领取）完成任务，不重复建书。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        cand = IntakeCandidate(work_item_id=item.id, title="恢复探针",
                               status="pending_review", photo_ids="[]")
        s.add(cand)
        s.commit()
        command = wf.confirm_candidates(s, item.id, [cand.id],
                                        decided_by_member_id=None)[0]
        command.status = "executing"
        command.lease_owner = "crashed-worker"
        command.lease_expires_at = wf._now() + timedelta(seconds=30)
        item.status = "executing"
        s.commit()
        item_id, command_id = item.id, command.id

    with SessionLocal() as s:
        command = s.scalars(select(IntakeCommandExecution)).first()
        command.lease_expires_at = wf._now() - timedelta(seconds=1)
        s.commit()

        with patch("app.services.intake.fetch_metadata", return_value=None):
            result = wf.execute_work_item(s, item_id)
        assert result["status"] == "completed"
        command = s.scalars(select(IntakeCommandExecution)).first()
        assert command.status == "completed"
        books = s.scalars(select(Book)).fetchall()
        assert len(books) == 1  # 恢复不产生重复书目


def test_retry_with_unexpired_lease_keeps_task_executing(db_engine):
    """单命令重试（BUG-283 路径）撞上未过期租约：重算后任务仍是 executing。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        cand = IntakeCandidate(work_item_id=item.id, title="恢复探针",
                               status="pending_review", photo_ids="[]")
        s.add(cand)
        s.commit()
        command = wf.confirm_candidates(s, item.id, [cand.id],
                                        decided_by_member_id=None)[0]
        command.status = "executing"
        command.lease_owner = "crashed-worker"
        command.lease_expires_at = wf._now() + timedelta(seconds=30)
        item.status = "executing"
        s.commit()
        command_id = command.id

        wf.retry_execution(s, command_id)
        command = s.scalars(select(IntakeCommandExecution)).first()
        assert command.status == "executing"  # 未接管活跃租约
        assert wf.get_work_item(s, item.id).status == "executing"


# ── BUG-286：人工来源的 v1 候选（拆分/手工构建）不被识别重建清空 ──

def _failing_vision():
    return patch("app.services.vision.recognize_cover_fields", return_value=VisionServiceResult(
        ok=False, error_code="timeout", message="模型超时"))


def test_split_v1_candidate_inheriting_user_fields_survives_recognize(db_engine):
    """候选保存人工修正（v2）→ 拆出 v1 候选（继承人工字段与 evidence source=user）
    → 重识别局部失败（超时）重建：拆出候选保留人工字段，其照片不进入新分组。"""
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
        {"photo_id": "p0002", "filename": "b.png", "content": _png_bytes((2, 2, 2)),
         "role": "cover"},
    ])
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="模型初稿", confidence=0.9),
            "p0002": wf.RecognitionInput(title="模型初稿", confidence=0.8),
        })[0]
        wf.update_candidate(s, cand.id, title="人工修正书名", authors=["人工修正作者"])
        original, split = wf.split_candidate(s, cand.id, photo_ids_to_new=["p0002"])
        assert split.version == 1
        assert json.loads(split.evidence)["title"]["source"] == "user"

        with _failing_vision():
            wf.recognize_photos(s, item_id)
        s.expire_all()
        live = {tuple(json.loads(c.photo_ids)): c for c in _candidates_of(s, item_id)
                if c.status == "pending_review"}
        assert live[("p0001",)].title == "人工修正书名"
        assert live[("p0002",)].title == "人工修正书名"  # 拆出候选未被空白候选替换
        assert json.loads(live[("p0002",)].authors or "[]") == ["人工修正作者"]


def test_manually_built_user_source_v1_candidate_survives_recognize(db_engine):
    """手工构建接口直接保存的 source=user、version=1 候选同样受保护。"""
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(),
         "role": "cover"},
    ])
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="手工录入", source="user")})
        with _failing_vision():
            wf.recognize_photos(s, item_id)
        s.expire_all()
        titles = [c.title for c in _candidates_of(s, item_id)
                  if c.status == "pending_review"]
        assert titles == ["手工录入"]


def test_vision_only_v1_candidate_still_rebuilt_on_recognize(db_engine):
    """保护不能过宽：纯模型来源的 v1 候选仍应被新识别结果取代。"""
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(),
         "role": "cover"},
    ])
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="旧识别")})
        ok = VisionServiceResult(
            ok=True, candidate=VisionCandidate(title="新识别", confidence=0.9))
        with patch("app.services.vision.recognize_cover_fields", return_value=ok):
            wf.recognize_photos(s, item_id)
        s.expire_all()
        titles = [c.title for c in _candidates_of(s, item_id)
                  if c.status == "pending_review"]
        assert titles == ["新识别"]


# ── BUG-285：确认与重建并发 → 提交时归属复验拒绝 ──

def test_confirm_before_rebuild_preserves_claimed_photo(db_engine):
    """确认持有写锁时重建等待，提交后重建看到已确认照片，不创建重复候选。"""
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(),
         "role": "cover"},
    ])
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        old_id = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="并发书甲")})[0].id

    started, finished = threading.Event(), threading.Event()
    errors, rebuilt = [], []

    def rebuild():
        try:
            with SessionLocal() as other:
                started.set()
                rebuilt.extend(wf.build_candidates(other, item_id, {
                    "p0001": wf.RecognitionInput(title="并发书丙")}))
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=rebuild)
    try:
        with SessionLocal() as db:
            real_commit = db.commit

            def commit_after_rebuild_attempt():
                thread.start()
                assert started.wait(5)
                assert not finished.wait(0.2)
                real_commit()

            with patch.object(db, "commit", side_effect=commit_after_rebuild_attempt):
                wf.confirm_candidates(db, item_id, [old_id], decided_by_member_id=None)
    finally:
        if thread.ident is not None:
            thread.join(10)
    assert not thread.is_alive()
    assert not errors, errors
    assert rebuilt == []

    with SessionLocal() as s:
        owners = [c for c in _candidates_of(s, item_id)
                  if "p0001" in json.loads(c.photo_ids) and c.status != "rejected"]
        assert len(owners) == 1, [(c.id, c.title, c.status) for c in owners]
        assert owners[0].id == old_id and owners[0].status == "confirmed"
        books = s.scalars(select(Book)).fetchall()
        assert books == []


def test_serial_rebuild_after_confirm_still_works(db_engine):
    """正常串行（确认完成后再重建）：已确认候选的照片不参与新分组，
    确认执行新候选后同一照片不会被建成第二本书（沿用上一轮修复语义）。"""
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
        {"photo_id": "p0002", "filename": "b.png", "content": _png_bytes((2, 2, 2)),
         "role": "cover"},
    ])
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="甲书", confidence=0.9),
            "p0002": wf.RecognitionInput(title="乙书", confidence=0.8),
        })
        first = next(c for c in _candidates_of(s, item_id) if c.title == "甲书")
        wf.confirm_candidates(s, item_id, [first.id], decided_by_member_id=1)

        cands = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="丙书（新识别）", confidence=0.9),
            "p0002": wf.RecognitionInput(title="乙书", confidence=0.8),
        })
        assert len(cands) == 1
        assert json.loads(cands[0].photo_ids) == ["p0002"]
        assert cands[0].title == "乙书"
        wf.confirm_candidates(s, item_id, [cands[0].id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        books = s.scalars(select(Book)).fetchall()
        assert sorted(b.title for b in books) == ["乙书", "甲书"]


def test_confirm_rejects_candidate_whose_photos_are_claimed(db_engine):
    """纵深防御：确认时所选候选的照片已被其他已确认/已执行候选占用（排除自身）
    → 拒绝（409 语义）；同一确认批次内两个候选共用照片同样拒绝。"""
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(),
         "role": "cover"},
    ])
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        first, second = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="占用者", confidence=0.9),
        })[0], None
        # 同一照片的手工关联候选（模拟拆分/重关联后的重叠归属）
        second = IntakeCandidate(
            work_item_id=item_id, version=1, status="pending_review",
            title="撞车者", photo_ids=json.dumps(["p0001"]))
        s.add(second)
        s.commit()
        wf.confirm_candidates(s, item_id, [first.id], decided_by_member_id=1)

        with pytest.raises(WorkflowError, match="并发|已存在"):
            wf.confirm_candidates(s, item_id, [second.id], decided_by_member_id=1)
        s.expire_all()
        assert s.get(IntakeCandidate, second.id).status == "pending_review"
        books = s.scalars(select(Book)).fetchall()
        assert books == []
