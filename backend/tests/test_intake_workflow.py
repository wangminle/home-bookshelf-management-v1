"""PLN-012 M3 回归：工作流服务（BI-12～15）。

覆盖：
- 协作对象链路：WorkItem → Photo → Candidate → ChangeSet/Decision → CommandExecution；
- 多图候选配对（相同 SHA/ISBN/书名）与拆分/重新关联（BI-13）；
- 既有书匹配预览与逐字段差异，预览零写入（BI-14）；
- 版本化确认（修改失格）、幂等回执（同键同参数重放、同键不同参数拒绝）、
  崩溃恢复按查重不重复建书、租约接管（BI-15）；
- 书目写入只经领域服务（prefer_confirmed）。
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Book, IntakeCandidate, IntakeCommandExecution, IntakePhoto
from app.services import intake_workflow as wf
from app.services.intake_workflow import (
    COMMAND_CREATE_BOOK,
    COMMAND_LINK_PHOTOS,
    WorkflowError,
)

ISBN_HUOZHE = "9787506365437"
ISBN_SANTI = "9787536692930"


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _mk_item_with_photos(db_engine, photos: list[dict]) -> tuple[int, list[str]]:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="测试批次")
        wf.add_photos(s, item.id, photos)
        return item.id, [p["photo_id"] for p in photos]


def _fake_png(content: bytes = b"\x89PNGfake", name: str = "c.png") -> dict:
    return {"photo_id": "p0001", "filename": name, "content": content, "role": "cover"}


# ── BI-12：协作对象与照片 ──

def test_work_item_and_photo_persistence(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="20261002 批次", created_by_member_id=7)
        assert item.id and item.status == "draft"
        wf.add_photos(s, item.id, [
            {"photo_id": "p0001", "filename": "a.jpg", "content": b"jpa", "role": "cover"},
            {"photo_id": "p0002", "filename": "b.png", "content": b"jpb", "role": "barcode"},
        ])
        photos = s.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == item.id)).fetchall()
        assert [p.photo_id for p in photos] == ["p0001", "p0002"]
        assert photos[0].sha256 and photos[0].role == "cover"
        assert (wf.photo_abs_path(photos[0])).is_file()
        assert item.status == "in_review"  # 上传后进入核对态


def test_duplicate_photo_id_rejected(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        wf.add_photos(s, item.id, [_fake_png()])
        with pytest.raises(WorkflowError, match="photo_id 已存在"):
            wf.add_photos(s, item.id, [
                {"photo_id": "p0001", "filename": "x.png", "content": b"zz", "role": "cover"}])


def test_missing_work_item_raises(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        with pytest.raises(WorkflowError, match="不存在"):
            wf.get_work_item(s, 99999)


# ── BI-13：候选配对建议 ──

def test_same_content_photos_grouped_into_one_candidate(db_engine):
    """内容完全相同的两张照片（重复拍摄）→ 同一候选（去重建议）。"""
    SessionLocal = _session(db_engine)
    content = b"identical-bytes"
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": content, "role": "cover"},
        {"photo_id": "p0002", "filename": "b.jpg", "content": content, "role": "cover"},
    ])
    with SessionLocal() as s:
        cands = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="活在此刻", authors=["X"], confidence=0.8),
            "p0002": wf.RecognitionInput(title="活在此刻", authors=["X"], confidence=0.7),
        })
        assert len(cands) == 1
        import json as _json
        assert _json.loads(cands[0].photo_ids) == ["p0001", "p0002"]
        assert cands[0].title == "活在此刻"


def test_same_isbn_groups_different_content(db_engine):
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"cover-img", "role": "cover"},
        {"photo_id": "p0002", "filename": "b.jpg", "content": b"barcode-img", "role": "barcode"},
    ])
    with SessionLocal() as s:
        cands = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="活着", authors=["余华"], confidence=0.9),
            "p0002": wf.RecognitionInput(isbn=ISBN_HUOZHE, confidence=0.6),
        })
        assert len(cands) == 1
        assert cands[0].isbn == ISBN_HUOZHE  # 条码照片的 ISBN 并入候选
        assert cands[0].title == "活着"


def test_different_books_stay_separate(db_engine):
    """书名不同且无公共键 → 两个候选（不因相似误合并）。"""
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img1", "role": "cover"},
        {"photo_id": "p0002", "filename": "b.jpg", "content": b"img2", "role": "cover"},
    ])
    with SessionLocal() as s:
        cands = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="翦商", authors=["李硕"]),
            "p0002": wf.RecognitionInput(title="小学问"),
        })
        assert len(cands) == 2


def test_split_candidate_moves_photos_and_bumps_version(db_engine):
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"},
        {"photo_id": "p0002", "filename": "b.jpg", "content": b"y", "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体"),
            "p0002": wf.RecognitionInput(title="三体"),
        })
        cand = s.scalars(select(IntakeCandidate)).first()

        original, new = wf.split_candidate(s, cand.id, photo_ids_to_new=["p0002"])
        assert original.status == "pending_review"
        assert original.version == 2
        import json as _json
        assert _json.loads(original.photo_ids) == ["p0001"]
        assert _json.loads(new.photo_ids) == ["p0002"]
        assert new.version == 1 and new.status == "pending_review"

    # BUG-294：已确认候选不可拆分（旧行为"拆分使确认失格"会把任务打成 failed）
    with SessionLocal() as s:
        wf.confirm_candidates(s, item_id, [new.id], decided_by_member_id=1)
        with pytest.raises(WorkflowError, match="已确认"):
            wf.split_candidate(s, new.id, photo_ids_to_new=["p0002"])


def test_update_candidate_invalid_isbn_rejected(db_engine):
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = s.scalars(select(IntakeCandidate)).first()
        with pytest.raises(WorkflowError, match="ISBN 校验位"):
            wf.update_candidate(s, cand.id, isbn="9787506365430")


def test_update_candidate_photo_ids_must_belong_to_item(db_engine):
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = s.scalars(select(IntakeCandidate)).first()
        with pytest.raises(WorkflowError, match="不属于本任务"):
            wf.update_candidate(s, cand.id, photo_ids=["ghost"])


def test_update_candidate_rejects_empty_photo_ids(db_engine):
    """BUG-315：编辑路径与拆分路径（BUG-293）同口径，不允许清空候选照片。

    空照片候选是永远无法完成的空壳，且因 _is_user_authored 保护不会被重建
    取代，任务会卡在非 completed 状态。
    """
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = s.scalars(select(IntakeCandidate)).first()
        with pytest.raises(WorkflowError, match="全部清空"):
            wf.update_candidate(s, cand.id, photo_ids=[])


# ── BI-14：匹配预览（只读）──

def test_preview_matches_no_write_and_field_diff(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        with patch("app.services.intake.fetch_metadata", return_value=None):
            existing = wf.intake_book(s, wf.IntakeInput(
                title="活着", author="余华", isbn=ISBN_HUOZHE))
        assert existing.action == "created"
        book_count_before = s.scalar(select(func.count()).select_from(Book))

    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="活着", authors=["余华"], isbn=ISBN_HUOZHE)})
        cand = s.scalars(select(IntakeCandidate)).first()
        results = wf.preview_matches(s, item_id)
        assert results[0]["match_book_id"] == existing.book.id
        assert results[0]["diff"] in (None, {})  # 字段一致无差异
        # 预览零写入
        assert s.scalar(select(func.count()).select_from(Book)) == book_count_before

        # 字段冲突可见
        wf.update_candidate(s, cand.id, title="许三观卖血记", authors=["余华2"])
        results = wf.preview_matches(s, item_id)
        diff = results[0]["diff"]
        assert diff["title"]["candidate"] == "许三观卖血记"
        assert diff["title"]["existing"] == "活着"


# ── BI-15：确认 + 幂等执行 ──

def _prepare_confirmed(db_engine, *, with_match=False):
    SessionLocal = _session(db_engine)
    if with_match:
        with SessionLocal() as s:
            with patch("app.services.intake.fetch_metadata", return_value=None):
                wf.intake_book(s, wf.IntakeInput(title="活着", author="余华", isbn=ISBN_HUOZHE))
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img-a", "role": "cover"},
        {"photo_id": "p0002", "filename": "b.jpg", "content": b"img-a", "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="活着", authors=["余华"], isbn=ISBN_HUOZHE, confidence=0.9),
            "p0002": wf.RecognitionInput(title="活着", authors=["余华"], confidence=0.8),
        })
        wf.preview_matches(s, item_id)
        cand = s.scalars(select(IntakeCandidate)).first()
        executions = wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        return item_id, cand.id, executions[0].id


def test_confirm_then_execute_creates_one_book_with_prefer_confirmed(db_engine):
    item_id, cand_id, exec_id = _prepare_confirmed(db_engine)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = s.get(IntakeCommandExecution, exec_id)
        assert execution.status == "completed" and execution.command_type == COMMAND_CREATE_BOOK
        assert execution.book_id
        result = __import__("json").loads(execution.result)
        assert result["photo_ids"] == ["p0001", "p0002"]
        # 候选终态
        cand = s.get(IntakeCandidate, cand_id)
        assert cand.status == "executed"


def test_execute_is_idempotent_replay_returns_existing_receipt(db_engine):
    """重复点击/重放：同命令不重复创建书目、副本、回执（验收锚点）。"""
    item_id, _, exec_id = _prepare_confirmed(db_engine)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        wf.execute_work_item(s, item_id)
        books_before = s.scalar(select(func.count()).select_from(Book))
    # 再执行两次（模拟重复点击/重试）
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        for _ in range(2):
            summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        assert s.scalar(select(func.count()).select_from(Book)) == books_before
        executions = s.scalars(select(IntakeCommandExecution)).fetchall()
        assert len(executions) == 1  # 同键只有一条回执
        assert executions[0].attempts == 1  # completed 幂等返回，不再尝试


def test_confirm_after_edit_binds_new_version_and_old_command_not_reused(db_engine):
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = s.scalars(select(IntakeCandidate)).first()
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        assert cand.version == 1

        # 确认后修改 → 版本 2 + 失去确认资格
        wf.update_candidate(s, cand.id, title="三体（纪念版）")
        assert cand.version == 2 and cand.status == "pending_review"

        # 用旧版本直接执行被拒：确认资格已失效（重新确认生成新命令）
        executions = wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        assert executions[0].params_hash  # 新命令基于版本 2
        with SessionLocal() as s2:
            all_exec = s2.scalars(select(IntakeCommandExecution)).fetchall()
            assert len({e.command_key for e in all_exec}) == 2


def test_link_photos_existing_book_default_no_field_update(db_engine):
    """确认已存在书：仅建立关联（+可选回填封面），不改正文书目字段。"""
    item_id, cand_id, exec_id = _prepare_confirmed(db_engine, with_match=True)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        books_before = s.scalar(select(func.count()).select_from(Book))
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = s.get(IntakeCommandExecution, exec_id)
        assert execution.command_type == COMMAND_LINK_PHOTOS
        assert s.scalar(select(func.count()).select_from(Book)) == books_before  # 不新建
        result = __import__("json").loads(execution.result)
        assert result["linked"] is True


def test_link_photos_target_deleted_fails_with_actionable_error(db_engine):
    item_id, cand_id, exec_id = _prepare_confirmed(db_engine, with_match=True)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        # 模拟目标书在确认后被删除
        from app.models import Book as BookModel
        target_id = s.get(IntakeCommandExecution, exec_id)
        cand = s.get(IntakeCandidate, cand_id)
        s.get(BookModel, cand.match_book_id)
        s.query(BookModel).filter(BookModel.id == cand.match_book_id).delete()
        s.commit()
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "failed"
        execution = s.get(IntakeCommandExecution, exec_id)
        assert execution.status == "failed"
        assert "不存在" in execution.error


def test_crash_recovery_completes_receipt_without_duplicate_book(db_engine):
    """崩溃恢复：执行中断后按查重恢复回执，绝不重复建书（验收锚点）。"""
    item_id, _, exec_id = _prepare_confirmed(db_engine)
    SessionLocal = _session(db_engine)

    # 模拟崩溃：书目已建（领域服务已提交），回执仍是 executing 且租约过期
    with SessionLocal() as s:
        with patch("app.services.intake.fetch_metadata", return_value=None):
            result = wf.intake_book(s, wf.IntakeInput(
                title="活着", authors=["余华"], isbn=ISBN_HUOZHE,
                field_policy="prefer_confirmed"))
        created_book_id = result.book.id
        execution = s.get(IntakeCommandExecution, exec_id)
        execution.status = "executing"
        execution.lease_owner = "dead-worker"
        execution.lease_expires_at = wf._now() - timedelta(seconds=1)
        s.commit()

    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        books_before = s.scalar(select(func.count()).select_from(Book))
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = s.get(IntakeCommandExecution, exec_id)
        assert execution.status == "completed"
        assert execution.book_id == created_book_id  # 恢复指向已建书目
        assert s.scalar(select(func.count()).select_from(Book)) == books_before  # 无重复
        assert __import__("json").loads(execution.result).get("recovered") is True


def test_live_lease_not_taken_over(db_engine):
    """活跃租约不被接管（另一个执行者进行中）。"""
    item_id, _, exec_id = _prepare_confirmed(db_engine)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        execution = s.get(IntakeCommandExecution, exec_id)
        execution.status = "executing"
        execution.lease_owner = "alive-worker"
        execution.lease_expires_at = wf._now() + timedelta(seconds=30)
        s.commit()

    with SessionLocal() as s:
        execution = s.get(IntakeCommandExecution, exec_id)
        wf.run_command_execution(s, execution)
        assert execution.status == "executing"  # 未被接管
        assert execution.lease_owner == "alive-worker"


def test_retry_failed_execution_allowed_completed_rejected(db_engine):
    item_id, _, exec_id = _prepare_confirmed(db_engine, with_match=True)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        from app.models import Book as BookModel
        cand = s.scalars(select(IntakeCandidate)).first()
        s.query(BookModel).filter(BookModel.id == cand.match_book_id).delete()
        s.commit()
        wf.execute_work_item(s, item_id)
        execution = s.get(IntakeCommandExecution, exec_id)
        assert execution.status == "failed"
        retried = wf.retry_execution(s, execution.id)
        assert retried.status == "failed"  # 目标仍不存在 → 仍失败，但可重试（非异常）

        # 完成后重试 = 幂等返回既有回执，不抛错
        with patch.object(wf, "_execute_link_photos",
                          return_value={"book_id": 9, "action": "exists", "linked": True,
                                        "cover_backfilled": False, "message": "x",
                                        "warnings": [], "photo_ids": []}):
            execution.status = "completed"
            s.commit()
            again = wf.retry_execution(s, execution.id)
            assert again.status == "completed"


def test_confirm_requires_title_or_isbn(db_engine):
    SessionLocal = _session(db_engine)
    item_id, _ = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput()})
        cand = s.scalars(select(IntakeCandidate)).first()
        with pytest.raises(WorkflowError, match="缺书名且缺 ISBN"):
            wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)


def test_execute_without_commands_rejected(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        with pytest.raises(WorkflowError, match="没有可执行命令"):
            wf.execute_work_item(s, item.id)


def test_created_book_uses_confirmed_fields_not_metadata(db_engine):
    """经工作台创建的书走 prefer_confirmed：确认书名不被元数据改写（翦商锚点）。"""
    SessionLocal = _session(db_engine)
    item_id, _, _ = _prepare_confirmed(db_engine)

    class _Meta:
        title = "To Live"
        subtitle = None
        isbn13 = ISBN_HUOZHE
        isbn10 = None
        authors = ["Yu Hua"]
        publisher = None
        publish_date = None
        page_count = None
        language = None
        category = None
        summary = None
        source = "openlibrary"
        openlibrary_id = None
        google_books_id = None
        extra = None
        cover_url = None

    with SessionLocal() as s:
        with patch("app.services.intake.fetch_metadata", return_value=_Meta()):
            summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = s.scalars(select(IntakeCommandExecution)).first()
        book = s.get(Book, execution.book_id)
        assert book.title == "活着"  # 确认值保留，不被 Revelation 式英文题覆盖
