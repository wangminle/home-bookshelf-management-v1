"""8 项修复复核（2026-10-05）残口回归：BUG-285/286/288/289。

- BUG-288：诊断事件的异常文本不得泄露百分号编码形态的密钥；
- BUG-289：租约未过期的 executing 命令被执行器跳过后，任务保持 executing，
  前端“恢复执行”入口不消失，租约过期后可继续恢复；
- BUG-286：拆分出的、继承人工字段（evidence.source=user）的 v1 候选，
  以及手工构建保存的 source=user 候选，不被识别重建清空；
- BUG-285：确认与重建在同任务内互斥，同一照片不会同时属于已确认候选与
  新待核对候选，确认侧亦校验照片未被其他确认候选占用。
"""
from __future__ import annotations

import io
import json
import threading
import urllib.error
from datetime import timedelta
from unittest.mock import patch

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import Book, IntakeCandidate, IntakeCommandExecution
from app.services import intake_workflow as wf
from app.services.metadata import http as metadata_http
from app.services.vision import VisionServiceResult


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _item_with_photos(db_engine, n: int = 1) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="复核回归")
        photos = []
        for i in range(1, n + 1):
            image = io.BytesIO()
            Image.new("RGB", (2, 2), (i, i, i)).save(image, format="PNG")
            photos.append({"photo_id": f"p{i}", "filename": f"{i}.png",
                           "content": image.getvalue(), "role": "cover"})
        wf.add_photos(s, item.id, photos)
        return item.id


# ── BUG-288 ──

@pytest.mark.parametrize("echoed_secret", [
    "synthetic%2Bsecret%2F2026%3D",
    "synthetic%2bsecret%2f2026%3d",
    "synthetic%2bsecret%2F2026%3D",
    "synthetic+secret/2026=",
])
def test_error_message_masks_percent_encoded_secret(echoed_secret):
    url = "https://example.invalid/books?api_key=synthetic%2Bsecret%2F2026%3D&q=isbn"
    echoed = "https://example.invalid/books?api_key=" + echoed_secret + "&q=isbn"
    events: list[dict] = []
    metadata_http.clear_response_listeners()
    metadata_http.add_response_listener(events.append)
    try:
        with patch.object(metadata_http.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("failed request " + echoed)):
            assert metadata_http.get_json(url) is None
    finally:
        metadata_http.clear_response_listeners()
    assert len(events) == 1
    blob = json.dumps(events[0], ensure_ascii=False)
    assert echoed_secret not in blob
    for leaked in ("synthetic%2Bsecret%2F2026%3D", "synthetic+secret/2026=",
                   "synthetic%2bsecret", "secret%2F2026"):
        assert leaked not in blob
    assert "api_key=***" in events[0]["url"]
    assert "q=isbn" in events[0]["error"]  # 非敏感参数保留，便于诊断


def test_error_message_masks_variants_decoded_plus_and_lowercase_hex():
    exc = RuntimeError(
        "key=a%2bb%2fc  decoded=a+b/c  plus=a+b%2Fc  plain=plainsecret")
    msg = metadata_http._error_message(
        exc, "https://x.invalid/?key=a%2bb%2fc&token=plainsecret&q=1")
    for leaked in ("a%2bb%2fc", "a+b/c", "a+b%2Fc", "plainsecret"):
        assert leaked not in msg


# ── BUG-289 ──

def test_unexpired_lease_keeps_task_executing_and_recoverable(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        cand = IntakeCandidate(work_item_id=item.id, title="恢复探针",
                               status="pending_review", photo_ids="[]")
        s.add(cand)
        s.commit()
        command = wf.confirm_candidates(s, item.id, [cand.id], decided_by_member_id=None)[0]
        command.status = "executing"
        command.lease_owner = "crashed-worker"
        command.lease_expires_at = wf._now() + timedelta(seconds=30)
        item.status = "executing"
        s.commit()
        item_id, command_id = item.id, command.id

        result = wf.execute_work_item(s, item_id)
        assert result["status"] == "executing"  # 不得判成 failed
        assert s.get(IntakeCommandExecution, command_id).status == "executing"

        # 租约过期后再次“恢复执行”：按查重/重新领取继续，不重复建书
        command = s.get(IntakeCommandExecution, command_id)
        command.lease_expires_at = wf._now() - timedelta(seconds=1)
        s.commit()
        with patch("app.services.intake.fetch_metadata", return_value=None):
            result = wf.execute_work_item(s, item_id)
        assert result["status"] == "completed", result


# ── BUG-286 ──

def _failing_vision():
    return patch("app.services.vision.recognize_cover_fields", return_value=VisionServiceResult(
        ok=False, error_code="timeout", message="模型超时"))


def test_split_candidate_inheriting_user_fields_survives_recognize(db_engine):
    item_id = _item_with_photos(db_engine, 2)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="模型初稿"),
            "p2": wf.RecognitionInput(title="模型初稿"),
        })[0]
        wf.update_candidate(s, cand.id, title="人工修正书名", authors=["人工作者"])
        _, split = wf.split_candidate(s, cand.id, photo_ids_to_new=["p2"])
        assert split.version == 1 and split.title == "人工修正书名"
        assert json.loads(split.evidence)["title"]["source"] == "user"
        with _failing_vision():
            wf.recognize_photos(s, item_id)
        s.expire_all()
        live = {tuple(json.loads(c.photo_ids)): c for c in s.scalars(
            select(IntakeCandidate).where(IntakeCandidate.work_item_id == item_id))
            if c.status == "pending_review"}
        assert set(live) == {("p1",), ("p2",)}
        assert live[("p1",)].title == "人工修正书名"
        assert live[("p2",)].title == "人工修正书名"  # 拆出候选未被空白候选替换


def test_manually_built_user_source_v1_candidate_survives_recognize(db_engine):
    item_id = _item_with_photos(db_engine, 1)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="手工录入", source="user")})
        with _failing_vision():
            wf.recognize_photos(s, item_id)
        s.expire_all()
        titles = [c.title for c in s.scalars(select(IntakeCandidate).where(
            IntakeCandidate.work_item_id == item_id)) if c.status == "pending_review"]
        assert titles == ["手工录入"]


def test_vision_only_candidate_still_rebuilt_on_recognize(db_engine):
    """保护不能过宽：纯模型来源的 v1 候选仍应被新识别结果取代。"""
    item_id = _item_with_photos(db_engine, 1)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p1": wf.RecognitionInput(title="旧识别")})
        from app.services.vision import VisionCandidate
        ok = VisionServiceResult(ok=True, candidate=VisionCandidate(title="新识别", confidence=0.9))
        with patch("app.services.vision.recognize_cover_fields", return_value=ok):
            wf.recognize_photos(s, item_id)
        s.expire_all()
        titles = [c.title for c in s.scalars(select(IntakeCandidate).where(
            IntakeCandidate.work_item_id == item_id)) if c.status == "pending_review"]
        assert titles == ["新识别"]


# ── BUG-285 ──

def test_confirm_and_rebuild_are_mutually_exclusive(db_engine):
    """重建先持有写锁，另一线程的旧候选确认等待，重建后被拒绝。"""
    item_id = _item_with_photos(db_engine, 1)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        old_id = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="并发书甲")})[0].id

    confirm_started = threading.Event()
    attempting_lock = threading.Event()
    confirm_finished = threading.Event()
    errors = []
    rejected = []
    real_lock = wf._lock_work_item

    def observed_lock(db, wid):
        if threading.current_thread() is thread:
            attempting_lock.set()
        return real_lock(db, wid)

    def confirm_in_thread():
        try:
            with SessionLocal() as other:
                confirm_started.set()
                wf.confirm_candidates(other, item_id, [old_id], decided_by_member_id=None)
        except wf.WorkflowError as exc:
            rejected.append(exc)
        except BaseException as exc:
            errors.append(exc)
        finally:
            confirm_finished.set()

    thread = threading.Thread(target=confirm_in_thread)
    with patch.object(wf, "_lock_work_item", side_effect=observed_lock):
        try:
            with SessionLocal() as db:
                real_scalars = db.scalars
                calls = 0

                def interleaved(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 3:  # 与复现一致：读完归属集合、准备清理 pending 时
                        thread.start()
                        assert confirm_started.wait(5) and attempting_lock.wait(5)
                        assert not confirm_finished.wait(0.2), "确认未等待重建释放任务锁"
                    return real_scalars(*args, **kwargs)

                with patch.object(db, "scalars", side_effect=interleaved):
                    new_id = wf.build_candidates(db, item_id, {
                        "p1": wf.RecognitionInput(title="并发书丙")})[0].id
        finally:
            # 即使重建断言失败，也先关闭会话释放锁，再等待确认线程退出。
            if thread.ident is not None:
                thread.join(10)
    assert not thread.is_alive()
    assert not errors, errors
    assert len(rejected) == 1

    with SessionLocal() as s:
        live = [c for c in s.scalars(select(IntakeCandidate).where(
            IntakeCandidate.work_item_id == item_id)) if c.status != "rejected"]
        owners = [c for c in live if "p1" in json.loads(c.photo_ids)]
        assert len(owners) == 1, [(c.id, c.title, c.status) for c in live]
        assert owners[0].id == new_id
        wf.confirm_candidates(s, item_id, [new_id], decided_by_member_id=None)
        with patch("app.services.intake.fetch_metadata", return_value=None):
            result = wf.execute_work_item(s, item_id)
        assert result["status"] == "completed"
        assert len(result["executions"]) == 1
        assert result["executions"][0]["result"]["photo_ids"] == ["p1"]
        books = list(s.scalars(select(Book)))
        assert [(b.id, b.title) for b in books] == [
            (result["executions"][0]["book_id"], "并发书丙")]
