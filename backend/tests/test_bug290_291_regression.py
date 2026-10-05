"""BUG-290/291 回归：跨步骤验证确认的两项缺陷。

- BUG-291：照片警告更新与候选分组保护解耦——受保护（人工编辑过）或已确认
  候选的照片，重识别时仍要写入 photo.warnings（失败记录）；识别成功时清除
  旧警告。此前归属过滤把这类照片一并排除在警告更新循环外，页面提示
  "见照片警告"却无任何警告，成功也无法清旧警告。
- BUG-290（前端流程，回归见 BatchIntakeView.spec.ts）此处锚定服务端契约：
  字段编辑清空 match_book_id/conflicts 后，携带 force_link 确认生成的是
  create_book 命令——前端必须在自动保存后重新预览核对目标。
"""
from __future__ import annotations

import json
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import IntakePhoto
from app.services import intake_workflow as wf
from app.services.vision import VisionCandidate, VisionServiceResult

ISBN_HUOZHE = "9787506365437"


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _item_with_photo(db_engine) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="警告回归")
        wf.add_photos(s, item.id, [
            {"photo_id": "p1", "filename": "a.jpg", "content": b"img", "role": "cover"}])
        return item.id


def _failing_vision(message: str = "模型超时"):
    return patch("app.services.vision.recognize_cover_fields", return_value=VisionServiceResult(
        ok=False, error_code="timeout", message=message))


def _ok_vision(title: str):
    return patch("app.services.vision.recognize_cover_fields", return_value=VisionServiceResult(
        ok=True, candidate=VisionCandidate(title=title, confidence=0.9)))


def _photo_of(s, item_id: int) -> IntakePhoto:
    return s.scalars(select(IntakePhoto).where(
        IntakePhoto.work_item_id == item_id)).one()


def test_recognize_failure_records_warning_on_user_edited_candidate_photo(db_engine):
    """BUG-291：人工编辑过（受保护）的候选，其照片识别失败仍要持久化警告。"""
    item_id = _item_with_photo(db_engine)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="三体")})[0]
        wf.update_candidate(s, cand.id, title="人工修正")  # v2 → 受保护
        with _failing_vision("识别服务超时"):
            summary = wf.recognize_photos(s, item_id)
        assert [f["photo_id"] for f in summary["failures"]] == ["p1"]
        warnings = json.loads(_photo_of(s, item_id).warnings)
        assert warnings and warnings[0]["code"] == "recognition_failed"
        assert warnings[0]["message"] == "识别服务超时"


def test_recognize_failure_records_warning_on_confirmed_candidate_photo(db_engine):
    """BUG-291：已确认候选的照片归属不参与重建分组，但警告同样要更新。"""
    item_id = _item_with_photo(db_engine)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="活着", isbn=ISBN_HUOZHE)})[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=None)
        with _failing_vision():
            summary = wf.recognize_photos(s, item_id)
        assert summary["failures"], "响应应包含该照片的失败记录"
        warnings = json.loads(_photo_of(s, item_id).warnings)
        assert warnings and warnings[0]["code"] == "recognition_failed"


def test_recognize_success_clears_stale_photo_warning(db_engine):
    """BUG-291 反向：识别成功必须清除旧警告，不能永久停留。"""
    item_id = _item_with_photo(db_engine)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p1": wf.RecognitionInput(title="三体")})
        with _failing_vision():
            wf.recognize_photos(s, item_id)
        assert json.loads(_photo_of(s, item_id).warnings)  # 先有旧警告
        with _ok_vision("新识别"):
            wf.recognize_photos(s, item_id)
        s.expire_all()
        assert json.loads(_photo_of(s, item_id).warnings) == []


def test_edited_candidate_with_force_link_selection_yields_create_book(db_engine):
    """BUG-290 服务端契约锚点：字段编辑清空 match/conflicts 后，即使确认请求
    携带 force_link，也生成 create_book 命令（无关联目标）——前端必须在自动
    保存后重新预览核对，不得沿用保存前的强制关联选择。"""
    from app.models import Book

    item_id = _item_with_photo(db_engine)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        wf.intake_book(s, wf.IntakeInput(title="活着", authors=["余华"], isbn=ISBN_HUOZHE))
        # 识别出同 ISBN 但书名明显不一致 → 预览产生归属冲突（用户所见）
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="许三观卖血记", isbn=ISBN_HUOZHE)})[0]
        wf.preview_matches(s, item_id)
        cand = s.get(wf.IntakeCandidate, cand.id)
        assert cand.match_book_id is not None
        assert json.loads(cand.conflicts or "{}").get("code") == "isbn_ownership_conflict"

        # 用户编辑字段（自动保存路径）→ match/conflicts 被清空
        wf.update_candidate(s, cand.id, title="许三观卖血记（修订）")
        executions = wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=None,
                                           resolutions={cand.id: "force_link"})
        assert executions[0].command_type == wf.COMMAND_CREATE_BOOK
        # 契约不变：执行侧最终由 ISBN 归属检查把关（create_book 遇同 ISBN 他书名失败）
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "failed"
        assert s.scalars(select(Book)).first() is not None  # 既有书未被污染
