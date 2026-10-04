"""BUG-265～283 回归：批量入库工作流缺陷修复。

覆盖：
- BUG-265：photo_id 路径穿越白名单拒绝（写入与读取两侧）+ 批量失败孤儿清理
  （含部分写入失败与数据库提交失败两个残留失败点）；
- BUG-266：匹配预览只处理 pending_review 候选，不改写已确认候选的匹配快照；
- BUG-267：superseded 旧版本命令不计入执行汇总，任务可达 completed；
  只确认部分候选时任务保持 partial，剩余候选仍可确认执行；
- BUG-268：确认后编辑回 pending 的候选被历史 ChangeSet/Decision 引用时
  重建安全软删除（无 FOREIGN KEY constraint failed）；
- BUG-269：省略 photo_ids 时自动序号按既有最大 p<序号> 续号（API）；
- BUG-272：编辑候选内容字段使既有 match_book_id / conflicts 失效；
- BUG-276：确认字段集合保留显式空值（subtitle='' / authors=[] 透传执行载荷）；
- BUG-280：空 candidate_ids 确认显式拒绝（400）；resolutions 非数字键 400；
- BUG-282：重建后软删除（rejected）的旧候选不可确认/编辑/拆分，其未执行
  命令一并 superseded，不再实际入库过时识别结果；
- BUG-283：单命令重试成功后按当前有效版本重算任务汇总状态。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Book, IntakeCandidate, IntakeCommandExecution, IntakePhoto
from app.services import intake_workflow as wf
from app.services.intake_workflow import WorkflowError

ISBN_HUOZHE = "9787506365437"


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _png_bytes(color=(1, 2, 3)) -> bytes:
    """真实可解码 PNG：无 ISBN 候选执行时会走条码识别，假字节会被判图片损坏。"""
    import io
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


def _mk_existing_huozhe(db_engine) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        result = wf.intake_book(s, wf.IntakeInput(title="活着", authors=["余华"],
                                                  isbn=ISBN_HUOZHE))
        return result.book.id


# ── BUG-265：photo_id 路径穿越与孤儿清理 ──

def test_photo_id_path_traversal_rejected_and_legit_uploads_work(db_engine):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        s.commit()
        item_id = item.id
    with SessionLocal() as s:
        for bad_pid in ("../../escaped", "a/b", "..", "x" * 65, "a b"):
            with pytest.raises(WorkflowError, match="photo_id 不合法"):
                wf.add_photos(s, item_id, [
                    {"photo_id": bad_pid, "filename": "a.jpg",
                     "content": b"x", "role": "cover"}])
        # 白名单内常规 id 正常上传
        saved = wf.add_photos(s, item_id, [
            {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"},
            {"photo_id": "cover-2_X", "filename": "b.png", "content": b"y", "role": "cover"}])
        assert [p.photo_id for p in saved] == ["p0001", "cover-2_X"]
        assert wf.photo_abs_path(saved[0]).is_file()
        # 越界写入未发生（旧实现会落盘 data_dir/intake_photos/escaped.jpg）
        assert not (settings.data_dir / "intake_photos" / "escaped.jpg").exists()
        assert not (settings.data_dir / "escaped.jpg").exists()


def test_photo_abs_path_rejects_out_of_root_file_path(db_engine):
    """读取侧纵深防御：DB 内 file_path 被篡改也不得越出 intake_photos 目录。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        saved = wf.add_photos(s, item.id, [
            {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"}])
        photo = s.get(IntakePhoto, saved[0].id)
        photo.file_path = "intake_photos/../../outside.jpg"
        s.commit()
        s.expire_all()
        with pytest.raises(WorkflowError, match="路径越界"):
            wf.photo_abs_path(photo)


def test_add_photos_write_failure_cleans_orphans(db_engine, monkeypatch):
    """批量写盘中途失败：先写的文件被清理，不留孤儿。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        s.commit()
        item_id = item.id
    first = settings.data_dir / "intake_photos" / str(item_id) / "p0001.jpg"
    real_write = Path.write_bytes
    calls = {"n": 0}

    def flaky_write(self, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write)
    with SessionLocal() as s:
        with pytest.raises(OSError, match="disk full"):
            wf.add_photos(s, item_id, [
                {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"},
                {"photo_id": "p0002", "filename": "b.jpg", "content": b"y", "role": "cover"}])
        assert not first.exists()
        assert s.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == item_id)).fetchall() == []


def test_add_photos_partial_write_failure_cleans_current_file(db_engine, monkeypatch):
    """BUG-265 残留：write_bytes 写出部分内容后抛错——正在写的半截文件也必须清理。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        s.commit()
        item_id = item.id
    photos_dir = settings.data_dir / "intake_photos" / str(item_id)
    real_write = Path.write_bytes
    calls = {"n": 0}

    def partial_write(self, data):
        calls["n"] += 1
        if calls["n"] == 2:
            real_write(self, data[:1])  # 第二张写出一个字节后磁盘满
            raise OSError("disk full")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", partial_write)
    with SessionLocal() as s:
        with pytest.raises(OSError, match="disk full"):
            wf.add_photos(s, item_id, [
                {"photo_id": "p0001", "filename": "a.jpg", "content": b"ab", "role": "cover"},
                {"photo_id": "p0002", "filename": "b.jpg", "content": b"uv", "role": "cover"}])
        # 第一张（已完整写入）与第二张（半截）都要清理
        assert not (photos_dir / "p0001.jpg").exists()
        assert not (photos_dir / "p0002.jpg").exists()
        assert s.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == item_id)).fetchall() == []


def test_add_photos_commit_failure_cleans_written_files(db_engine, monkeypatch):
    """BUG-265 残留：数据库提交失败——已写文件必须全部清理，不留无记录孤儿。"""
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        s.commit()
        item_id = item.id
    photos_dir = settings.data_dir / "intake_photos" / str(item_id)

    with SessionLocal() as s:
        def fail_commit():
            s.rollback()
            raise OSError("db commit failed")

        monkeypatch.setattr(s, "commit", fail_commit)
        with pytest.raises(OSError, match="db commit failed"):
            wf.add_photos(s, item_id, [
                {"photo_id": "p0001", "filename": "a.jpg", "content": b"x", "role": "cover"},
                {"photo_id": "p0002", "filename": "b.jpg", "content": b"y", "role": "cover"}])
    assert not (photos_dir / "p0001.jpg").exists()
    assert not (photos_dir / "p0002.jpg").exists()
    with SessionLocal() as s:
        assert s.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == item_id)).fetchall() == []
        assert wf.get_work_item(s, item_id).status == "draft"  # 状态未推进


def test_photo_id_traversal_rejected_via_api(client):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.jpg", b"x", "image/jpeg"))],
                    data={"photo_ids": "../../escaped"}, headers=headers)
    assert r.status_code == 400, r.text
    assert "photo_id 不合法" in r.text


# ── BUG-269：自动 photo_id 续号 ──

def test_auto_photo_ids_continue_after_second_upload_round(client):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", b"a", "image/png")),
                           ("files", ("b.png", b"b", "image/png"))],
                    headers=headers)  # 省略 photo_ids
    assert r.status_code == 200, r.text
    assert [p["photo_id"] for p in r.json()["data"]["added"]] == ["p0001", "p0002"]
    # 第二轮上传：自动序号从既有最大值续号，不与 p0001/p0002 冲突
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("c.png", b"c", "image/png"))],
                    headers=headers)
    assert r.status_code == 200, r.text
    assert [p["photo_id"] for p in r.json()["data"]["added"]] == ["p0003"]


# ── BUG-272：编辑使既有匹配/冲突失效 ──

def test_edit_candidate_invalidates_match_and_conflicts(db_engine):
    existing_id = _mk_existing_huozhe(db_engine)
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", authors=["刘慈欣"],
                                         isbn=ISBN_HUOZHE, confidence=0.9)})
        results = wf.preview_matches(s, item_id)
        assert results[0]["match_book_id"] == existing_id
        cand = _candidates_of(s, item_id)[0]
        assert json.loads(cand.conflicts)["code"] == "isbn_ownership_conflict"

        # 编辑为完全不同的书（并清 ISBN）→ 匹配与冲突解决结果全部失效
        wf.update_candidate(s, cand.id, title="许三观卖血记", isbn="")
        cand = s.get(IntakeCandidate, cand.id)
        assert cand.match_book_id is None
        assert cand.match_diff is None
        assert cand.conflicts is None

        # 重新确认 → create_book（而非 link 到旧书 A）→ 执行真正建出新书 B
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        books = s.scalars(select(Book)).fetchall()
        assert len(books) == 2
        new_book = next(b for b in books if b.id != existing_id)
        assert new_book.title == "许三观卖血记"


# ── BUG-266：预览只处理 pending_review 候选 ──

def test_preview_skips_confirmed_candidates(db_engine):
    """已确认候选的 match 快照不被预览改写 → 命令参数摘要不变 → 执行不 stale。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="活着", authors=["余华"], isbn=ISBN_HUOZHE)})
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        assert cand.match_book_id is None  # 未预览即确认 → create_book 命令

    # 确认之后出现同 ISBN 的既有书：预览若改写给候选的 match_book_id，
    # 命令参数摘要变化 → 执行将 stale_confirmation（旧 BUG-266 行为）。
    existing_id = _mk_existing_huozhe(db_engine)
    with SessionLocal() as s:
        results = wf.preview_matches(s, item_id)
        assert results == []  # 无 pending_review 候选
        cand = _candidates_of(s, item_id)[0]
        assert cand.match_book_id is None  # 已确认候选的匹配快照未被改写
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = s.scalars(select(IntakeCommandExecution)).first()
        assert execution.status == "completed"
        assert execution.error_code is None
        assert execution.book_id == existing_id  # 查重命中既有书，幂等完成


# ── BUG-268：重建不删有历史引用的 pending 候选 ──

def test_rebuild_supersedes_pending_candidate_with_history(db_engine):
    """确认→编辑回 pending（已有 ChangeSet/Decision 引用）→ 重建候选不得 FK 报错。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
        {"photo_id": "p0002", "filename": "b.png", "content": _png_bytes((2, 2, 2)),
         "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="活着", authors=["余华"], confidence=0.9),
            "p0002": wf.RecognitionInput(title="三体", authors=["刘慈欣"], confidence=0.8),
        })
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        # 确认后编辑 → 回到 pending_review，但已被历史 ChangeSet/Decision 引用
        wf.update_candidate(s, cand.id, title="活着（修订版）")
        cand = s.get(IntakeCandidate, cand.id)
        assert cand.status == "pending_review"

        # 重建（识别重跑同路径）：旧候选软删除保留历史行，新候选正常生成
        cands = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="活着（修订版）", authors=["余华"],
                                         confidence=0.9),
            "p0002": wf.RecognitionInput(title="三体", authors=["刘慈欣"], confidence=0.8),
        })
        assert len(cands) == 2
        old = s.get(IntakeCandidate, cand.id)
        assert old.status == "rejected"  # 有历史引用 → 软删除而非物理删除
        # 历史 ChangeSet/Decision 行完好（FK 未破坏）
        from app.models import IntakeChangeSet, IntakeDecision
        assert s.scalar(select(func.count()).select_from(IntakeChangeSet)) == 1
        assert s.scalar(select(func.count()).select_from(IntakeDecision)) == 1


# ── BUG-267：superseded 命令与完成判定 ──

def test_item_reaches_completed_after_reconfirm(db_engine):
    """确认→编辑→再确认→执行：旧命令 superseded 不计入，任务 completed。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        wf.update_candidate(s, cand.id, title="三体（纪念版）")
        cand = s.get(IntakeCandidate, cand.id)
        assert cand.status == "pending_review"
        executions = wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        new_exec_id = executions[0].id
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"  # 不被旧 stale 命令锁成 partial
        executions = s.scalars(select(IntakeCommandExecution)).fetchall()
        assert len(executions) == 2
        assert sum(1 for e in executions if e.status == "superseded") == 1
        new_exec = s.get(IntakeCommandExecution, new_exec_id)
        assert new_exec.status == "completed"
        assert s.get(Book, new_exec.book_id).title == "三体（纪念版）"


def test_partial_completion_does_not_lock_remaining_candidates(db_engine):
    """只确认 2 候选中的 1 个并执行：任务 partial，剩余候选仍可确认并最终 completed。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes((1, 1, 1)),
         "role": "cover"},
        {"photo_id": "p0002", "filename": "b.png", "content": _png_bytes((2, 2, 2)),
         "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", authors=["刘慈欣"]),
            "p0002": wf.RecognitionInput(title="活着", authors=["余华"]),
        })
        cands = _candidates_of(s, item_id)
        first_id = next(c.id for c in cands if c.title == "三体")
        second_id = next(c.id for c in cands if c.title == "活着")
        wf.confirm_candidates(s, item_id, [first_id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        # BUG-267(b)：不得因唯一命令完成而整单 completed（尚有 pending 候选）
        assert summary["status"] == "partial"
    with SessionLocal() as s:
        item = wf.get_work_item(s, item_id)
        assert item.status == "partial"
        # 剩余候选仍可确认（未被 completed 状态锁死）
        wf.confirm_candidates(s, item_id, [second_id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        assert s.scalar(select(func.count()).select_from(Book)) == 2


# ── BUG-280：空确认列表与 resolutions 键 ──

def test_confirm_empty_candidate_ids_rejected(db_engine, client):
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s)
        s.commit()
        item_id = item.id
        with pytest.raises(WorkflowError, match="candidate_ids 为空"):
            wf.confirm_candidates(s, item_id, [], decided_by_member_id=1)
        assert wf.get_work_item(s, item_id).status != "confirmed"  # 无副作用

    headers = {"Origin": "http://127.0.0.1"}
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/confirm",
                    json={"candidate_ids": []}, headers=headers)
    assert r.status_code == 400, r.text


def test_confirm_non_numeric_resolutions_key_rejected(db_engine):
    """resolutions 非数字键：400 级 WorkflowError，不再冒裸 ValueError（500）。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = _candidates_of(s, item_id)[0]
        with pytest.raises(WorkflowError, match="resolutions 的键必须是候选 ID"):
            wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1,
                                  resolutions={"abc": "force_link"})


# ── BUG-276：确认空值字段透传到执行载荷 ──

def test_confirmed_empty_values_preserved_in_execution_payload(db_engine):
    """显式清空 subtitle=''/authors=[]：确认字段集合保留空值并透传 intake 载荷。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", subtitle="地球往事三部曲之一",
                                         authors=["刘慈欣"])})
        cand = _candidates_of(s, item_id)[0]
        # 用户核对时显式清空副题与作者
        wf.update_candidate(s, cand.id, subtitle="", authors=[])
        cand = s.get(IntakeCandidate, cand.id)
        assert cand.subtitle == ""
        assert json.loads(cand.authors) == []
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)

    captured: dict = {}

    class _Book:
        id = 4242

    class _Result:
        book = _Book()
        action = "created"
        message = "ok"
        matched_source = "manual"
        isbn_detected = None
        warnings = []

    def fake_intake_book(db, payload, finalize=None):
        captured["payload"] = payload
        result = _Result()
        if finalize is not None:
            finalize(result)
        return result

    with SessionLocal() as s, \
            patch.object(wf, "intake_book", side_effect=fake_intake_book):
        summary = wf.execute_work_item(s, item_id)
    assert summary["status"] == "completed"
    payload = captured["payload"]
    # 空字符串/空列表是合法确认值（清空语义），不得被丢弃
    assert "subtitle" in payload.confirmed_fields
    assert "authors" in payload.confirmed_fields
    assert payload.subtitle == ""
    assert payload.authors == []
    assert "title" in payload.confirmed_fields and payload.title == "三体"


# ── BUG-282：软删除候选不可再确认/编辑/拆分，旧命令退出流程 ──

def test_rejected_candidate_cannot_be_reconfirmed_or_executed(db_engine):
    """重建后软删除（rejected）的旧候选：确认/编辑/拆分均拒绝；其未执行命令
    superseded，整批执行不得把过时识别结果真正入库。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        # 构建甲 → 确认 → 编辑回 pending（被历史 ChangeSet/Decision 引用）→ 重建为乙
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="甲（过时识别）", confidence=0.9)})
        old = _candidates_of(s, item_id)[0]
        old_id = old.id
        wf.confirm_candidates(s, item_id, [old_id], decided_by_member_id=1)
        wf.update_candidate(s, old_id, title="编辑后甲")

        cands = wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="乙（重建结果）", confidence=0.9)})
        assert len(cands) == 1
        new = cands[0]
        old = s.get(IntakeCandidate, old_id)
        assert old.status == "rejected"
        # 旧候选的 pending 命令随软删除退出流程，不会在整批执行时重放
        old_exec = s.scalars(select(IntakeCommandExecution).join(
            wf.IntakeChangeSet).where(wf.IntakeChangeSet.candidate_id == old_id)).first()
        assert old_exec is not None and old_exec.status == "superseded"

        # 软删除候选不可再确认（400 级 WorkflowError），也不可编辑/拆分
        with pytest.raises(WorkflowError, match="已被重建取代"):
            wf.confirm_candidates(s, item_id, [old_id], decided_by_member_id=1)
        with pytest.raises(WorkflowError, match="不可编辑"):
            wf.update_candidate(s, old_id, title="复活尝试")
        with pytest.raises(WorkflowError, match="不可拆分"):
            wf.split_candidate(s, old_id, photo_ids_to_new=["p0001"])

        # 新候选正常确认并执行
        wf.confirm_candidates(s, item_id, [new.id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        books = s.scalars(select(Book)).fetchall()
        assert len(books) == 1
        assert books[0].title == "乙（重建结果）"  # 甲未因旧命令被执行而入库


# ── BUG-283：单命令重试成功后重算任务汇总状态 ──

def test_retry_success_recomputes_item_status(db_engine):
    """首次执行临时失败（任务 failed）→ 依赖恢复后单命令重试成功 →
    命令 completed 且任务按当前有效版本重算为 completed，不再停留 failed。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
    with SessionLocal() as s, patch.object(wf, "intake_book",
                                           side_effect=ValueError("临时故障")):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "failed"
        execution = s.scalars(select(IntakeCommandExecution)).first()
        assert execution.status == "failed"
        exec_id = execution.id
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        retried = wf.retry_execution(s, exec_id)
        assert retried.status == "completed"
        assert retried.book_id is not None
        # BUG-283 核心：重试成功后任务状态同步重算，不再停留在 failed
        assert wf.get_work_item(s, item_id).status == "completed"
        assert s.scalars(select(Book)).fetchall() != []


def test_retry_failure_keeps_item_failed(db_engine):
    """对照：重试仍失败时任务保持 failed，状态重算不误报成功。"""
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
    with SessionLocal() as s, patch.object(wf, "intake_book",
                                           side_effect=ValueError("持续故障")):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "failed"
        exec_id = s.scalars(select(IntakeCommandExecution.id)).first()
    with SessionLocal() as s, patch.object(wf, "intake_book",
                                           side_effect=ValueError("持续故障")):
        retried = wf.retry_execution(s, exec_id)
        assert retried.status == "failed"
        assert wf.get_work_item(s, item_id).status == "failed"
