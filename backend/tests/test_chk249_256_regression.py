"""PLN-012 复核缺陷回归：BUG-249～256（后端）。

覆盖（复核定位于 intake_workflow / intake 服务与 API）：
- BUG-249：空文件/无文件上传必须 4xx（不得 200 + added=[]）；
- BUG-250：photo_to_dict 的 image_url 为 API-base 相对路径（不含 /api/v1，
  前端 ${BASE}${image_url} 契约）；
- BUG-251：确认副题贯通——无元数据与冲突元数据下均保留；
- BUG-252：执行绑定确认快照，候选被编辑后旧命令 stale_confirmation 拒绝；
- BUG-253：租约原子条件领取；被接管者不得提交回执/清除他人租约；
- BUG-254：ISBN 归属冲突贯穿预览/确认/执行，须显式 force_link 人工解决；
- BUG-255：照片图片端点仅 Owner Web 会话（Member 403 / 匿名 401）；
- BUG-256：批内停用/撤权阻断后续命令（副作用前 + 提交时双核验）。
"""
from __future__ import annotations

import json
import threading
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.models import (
    Book,
    IntakeCandidate,
    IntakeCommandExecution,
    IntakePhoto,
    Member,
)
from app.services import agent_access, intake_workflow as wf
from app.services.intake_workflow import WorkflowAuthError, WorkflowError

ISBN_HUOZHE = "9787506365437"  # 《活着》
ISBN_SANTI = "9787536692930"  # 《三体》


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _png_bytes(color=(1, 2, 3)) -> bytes:
    """真实可解码 PNG：无 ISBN 候选执行时会走条码识别，假字节会被判图片损坏。"""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


def _mk_item_with_photos(db_engine, photos: list[dict]) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="回归批次")
        wf.add_photos(s, item.id, photos)
        return item.id


def _candidates_of(s, item_id) -> list[IntakeCandidate]:
    return list(s.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == item_id)))


def _first_execution(s, item_id) -> IntakeCommandExecution:
    from app.models import IntakeChangeSet
    return s.scalars(select(IntakeCommandExecution).join(IntakeChangeSet).where(
        IntakeChangeSet.work_item_id == item_id)).first()


# ── BUG-249：空文件/无文件上传必须 4xx ──

def test_upload_without_files_returns_400(client, db_session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    # 无文件 multipart
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[], data={"photo_ids": "p0001"}, headers=headers)
    assert r.status_code == 400
    assert "未收到任何上传文件" in r.text
    # 完全不带 files 字段
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    data={"photo_ids": "p0001"}, headers=headers)
    assert r.status_code == 400
    # JSON Content-Type（前端旧缺陷形态）不得静默成功
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    json={"photo_ids": ["p0001"]}, headers=headers)
    assert r.status_code == 400
    # 持久化数量为零
    assert db_session.scalar(select(func.count()).select_from(IntakePhoto)) == 0


def test_upload_empty_content_file_returns_400(client, db_session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", b"", "image/png"))],
                    data={"photo_ids": "p0001"}, headers=headers)
    assert r.status_code == 400
    assert db_session.scalar(select(func.count()).select_from(IntakePhoto)) == 0


# ── BUG-250：image_url 为 API-base 相对路径 ──

def test_photo_image_url_is_api_base_relative(client, db_session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", b"png-bytes", "image/png"))],
                    data={"photo_ids": "p0001"}, headers=headers)
    assert r.status_code == 200, r.text
    added = r.json()["data"]["added"]
    assert len(added) == 1
    url = added[0]["image_url"]
    assert url.startswith("/intake-workflow/photos/")  # 不含 /api/v1
    assert "/api/v1/api/v1" not in url
    # 前端拼接契约：BASE(/api/v1) + image_url 可读
    r = client.get(f"/api/v1{url}")
    assert r.status_code == 200
    assert r.content == b"png-bytes"


# ── BUG-251：确认副题贯通 ──

def test_confirmed_subtitle_preserved_without_metadata(db_engine):
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", subtitle="地球往事三部曲之一",
                                         authors=["刘慈欣"])})
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = _first_execution(s, item_id)
        book = s.get(Book, execution.book_id)
        assert book.title == "三体"
        assert book.subtitle == "地球往事三部曲之一"  # 无元数据时确认副题保留


def test_confirmed_subtitle_not_overwritten_by_conflicting_metadata(db_engine):
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img", "role": "cover"}])

    class _Meta:
        title = "三体"
        subtitle = "The Three-Body Problem"
        isbn13 = ISBN_SANTI
        isbn10 = None
        authors = ["刘慈欣"]
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
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", subtitle="地球往事三部曲之一",
                                         authors=["刘慈欣"], isbn=ISBN_SANTI)})
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=_Meta()):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = _first_execution(s, item_id)
        book = s.get(Book, execution.book_id)
        assert book.subtitle == "地球往事三部曲之一"  # 冲突元数据不覆盖确认副题
        result = json.loads(execution.result)
        conflict = [w for w in result["warnings"] if w["code"] == "metadata_field_conflict"]
        assert any(w.get("field") == "subtitle" for w in conflict)


def test_intake_json_api_passes_subtitle(db_engine, client, db_session):
    """API/领域输入贯通：POST /intake/json 的 subtitle 落库保留。"""
    with patch("app.services.intake.fetch_metadata", return_value=None):
        r = client.post("/api/v1/books/intake/json", json={
            "title": "三体", "subtitle": "地球往事三部曲之一",
            "authors": ["刘慈欣"], "isbn": ISBN_SANTI,
            "field_policy": "prefer_confirmed",
        })
    assert r.status_code in (200, 201), r.text
    book_id = r.json()["data"]["book"]["id"]
    assert db_session.get(Book, book_id).subtitle == "地球往事三部曲之一"


# ── BUG-252：执行绑定确认快照 ──

def test_stale_command_rejected_until_reconfirmed(db_engine):
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.png", "content": _png_bytes(), "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {"p0001": wf.RecognitionInput(title="三体")})
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        old_exec_id = _first_execution(s, item_id).id
        # 确认后编辑 → 新版本 pending_review，旧命令失格
        wf.update_candidate(s, cand.id, title="三体（纪念版）")
        assert cand.version == 2

    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "failed"
        stale = s.get(IntakeCommandExecution, old_exec_id)
        assert stale.status == "failed"
        assert stale.error_code == "stale_confirmation"
        assert "重新确认" in stale.error
        # 旧命令不得按当前候选真实建书
        assert s.scalar(select(func.count()).select_from(Book)) == 0

    # 重新确认生成新命令后可执行
    with SessionLocal() as s:
        cand = _candidates_of(s, item_id)[0]
        executions = wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        new_exec_id = executions[0].id
        assert new_exec_id != old_exec_id
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        # BUG-267：旧版本命令已标记 superseded 且不再计入汇总——新命令完成
        # 且无遗留待核对候选，任务应达 completed（此前被旧 stale 命令锁死为 partial）
        assert summary["status"] == "completed"
        stale = s.get(IntakeCommandExecution, old_exec_id)
        assert stale.status == "superseded"  # 旧版本命令被取代，不重放
        new_exec = s.get(IntakeCommandExecution, new_exec_id)
        assert new_exec.status == "completed"
        book = s.get(Book, new_exec.book_id)
        assert book.title == "三体（纪念版）"


# ── BUG-253：原子租约领取与被接管保护 ──

def _prepare_confirmed_item(db_engine, *, title="活着", authors=None, isbn=ISBN_HUOZHE,
                            member_id=1):
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"img-a", "role": "cover"},
        {"photo_id": "p0002", "filename": "b.jpg", "content": b"img-a", "role": "cover"},
    ])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title=title, authors=authors or ["余华"],
                                         isbn=isbn, confidence=0.9),
            "p0002": wf.RecognitionInput(title=title, authors=authors or ["余华"],
                                         confidence=0.8),
        })
        wf.preview_matches(s, item_id)
        cand = _candidates_of(s, item_id)[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=member_id)
        return item_id, _first_execution_id(s, item_id)


def _first_execution_id(s, item_id) -> int:
    return _first_execution(s, item_id).id


def test_atomic_claim_race_loser_does_not_execute(db_engine):
    """两个 Session 预加载 pending 命令：先领取者执行，后至者被条件更新拒接。"""
    item_id, exec_id = _prepare_confirmed_item(db_engine)
    SessionLocal = _session(db_engine)
    started = threading.Event()
    release = threading.Event()
    real_create = wf._execute_create_book

    def slow_create(db, cand, **kwargs):
        started.set()
        assert release.wait(10), "测试超时"
        return real_create(db, cand, **kwargs)

    outcomes: dict = {}

    def worker():
        try:
            with SessionLocal() as s1, \
                    patch.object(wf, "_execute_create_book", side_effect=slow_create), \
                    patch("app.services.intake.fetch_metadata", return_value=None):
                wf.run_command_execution(s1, s1.get(IntakeCommandExecution, exec_id))
        except Exception as exc:  # noqa: BLE001 —— 测试内断言用
            outcomes["error"] = f"{exc.__class__.__name__}: {exc}"

    # S2/S3 在 pending 时预加载旧快照（复核定位于"两个 Session 预加载后均可覆盖领取"）
    s2 = SessionLocal()
    stale_pending = s2.get(IntakeCommandExecution, exec_id)
    s3 = SessionLocal()
    stale_racer = s3.get(IntakeCommandExecution, exec_id)

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(10), "执行者未开始副作用"

    # S1 持有效租约：S2 不得接管活跃租约
    skipped = wf.run_command_execution(s2, stale_pending)
    assert skipped.status == "executing"
    release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert "error" not in outcomes, outcomes.get("error")

    # S3 旧快照（内存仍 pending）再尝试执行：原子条件领取拒接，不得重复副作用
    result = wf.run_command_execution(s3, stale_racer)
    assert result.status == "completed"
    assert result.attempts == 1  # 抢占失败者未领取、未执行
    s2.close()
    s3.close()
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Book)) == 1  # 恰好一次
        execution = s.get(IntakeCommandExecution, exec_id)
        assert execution.status == "completed" and execution.book_id


def test_taken_over_executor_cannot_commit_receipt(db_engine):
    """旧执行者副作用后才发现被接管：回执不得提交、不得清除新租约。"""
    item_id, exec_id = _prepare_confirmed_item(db_engine)
    SessionLocal = _session(db_engine)
    real_create = wf._execute_create_book
    state: dict = {"took_over": False}

    def fake_create(db, cand, **kwargs):
        if not state["took_over"]:
            state["took_over"] = True
            # 接管：租约过期 → 新执行者领取、真实建书并提交 completed 回执
            with SessionLocal() as s3:
                ex = s3.get(IntakeCommandExecution, exec_id)
                ex.lease_expires_at = wf._now() - timedelta(seconds=1)
                s3.commit()
            with SessionLocal() as s4, \
                    patch("app.services.intake.fetch_metadata", return_value=None):
                wf.run_command_execution(s4, s4.get(IntakeCommandExecution, exec_id))
                state["s2_book_id"] = s4.get(IntakeCommandExecution, exec_id).book_id
            assert state["s2_book_id"]
        # 旧执行者继续走真实创建（查重命中接管方所建书目）
        return real_create(db, cand, **kwargs)

    with SessionLocal() as s1, \
            patch.object(wf, "_execute_create_book", side_effect=fake_create), \
            patch("app.services.intake.fetch_metadata", return_value=None):
        result = wf.run_command_execution(s1, s1.get(IntakeCommandExecution, exec_id))
        # 被接管：回执归新租约方，旧执行者的结果不落库
        assert result.status == "completed"
        assert result.book_id == state["s2_book_id"]
        assert result.book_id != 0
        stored_result = json.loads(result.result)
        assert stored_result["book_id"] == state["s2_book_id"]
        assert result.lease_owner is None and result.lease_expires_at is None
    with SessionLocal() as s:
        # 副作用恰好一次；回执不被旧执行者错乱/重复
        assert s.scalar(select(func.count()).select_from(Book)) == 1
        execution = s.get(IntakeCommandExecution, exec_id)
        assert execution.attempts == 2  # S1 领取 + S2 领取
        assert execution.book_id == state["s2_book_id"]
        stored = json.loads(execution.result)
        assert stored.get("recovered") is not True  # 正常完成回执即可
        assert stored["book_id"] == state["s2_book_id"]


# ── BUG-254：ISBN 归属冲突三环节阻断 ──

def _make_existing_huozhe(db_engine) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        result = wf.intake_book(s, wf.IntakeInput(title="活着", authors=["余华"],
                                                  isbn=ISBN_HUOZHE))
        return result.book.id


def test_isbn_conflict_blocks_confirm_until_force_link(db_engine):
    existing_id = _make_existing_huozhe(db_engine)
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"santi", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", authors=["刘慈欣"],
                                         isbn=ISBN_HUOZHE, confidence=0.9)})
        results = wf.preview_matches(s, item_id)
        assert results[0]["match_book_id"] == existing_id  # 预览可见命中
        cand = _candidates_of(s, item_id)[0]
        conflict = json.loads(cand.conflicts)
        assert conflict["code"] == "isbn_ownership_conflict"
        assert conflict["existing_title"] == "活着"

        # 确认：无显式解决 → 拒绝
        with pytest.raises(WorkflowError, match="ISBN 归属冲突"):
            wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
        assert cand.status == "pending_review"  # 未自动放行

        # 显式人工解决：force_link
        executions = wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1,
                                           resolutions={cand.id: "force_link"})
        assert cand.status == "confirmed"
        assert json.loads(cand.conflicts)["resolved"] == "force_link"
    with SessionLocal() as s:
        books_before = s.scalar(select(func.count()).select_from(Book))
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "completed"
        execution = _first_execution(s, item_id)
        assert execution.book_id == existing_id  # 显式解决后允许关联
        assert s.scalar(select(func.count()).select_from(Book)) == books_before


def test_isbn_conflict_blocks_create_book_execution(db_engine):
    """未走预览（match_book_id 为空）的 create 路径：执行环节仍被领域阻断。"""
    _make_existing_huozhe(db_engine)
    SessionLocal = _session(db_engine)
    item_id = _mk_item_with_photos(db_engine, [
        {"photo_id": "p0001", "filename": "a.jpg", "content": b"santi", "role": "cover"}])
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p0001": wf.RecognitionInput(title="三体", authors=["刘慈欣"],
                                         isbn=ISBN_HUOZHE, confidence=0.9)})
        cand = _candidates_of(s, item_id)[0]
        assert cand.match_book_id is None  # 未预览
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=1)
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id)
        assert summary["status"] == "failed"
        execution = _first_execution(s, item_id)
        assert execution.status == "failed"
        assert execution.error_code == "isbn_ownership_conflict"
        assert s.scalar(select(func.count()).select_from(Book)) == 1  # 只有《活着》


def test_isbn_conflict_via_api_requires_resolution(client, db_session):
    from unittest.mock import patch as _patch
    with _patch("app.services.intake.fetch_metadata", return_value=None):
        r = client.post("/api/v1/books/intake/json", json={
            "title": "活着", "authors": ["余华"], "isbn": ISBN_HUOZHE})
    assert r.status_code in (200, 201), r.text
    existing_id = r.json()["data"]["book"]["id"]

    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                files=[("files", ("a.png", b"santi-img", "image/png"))],
                data={"photo_ids": "p0001"}, headers=headers)
    client.post(f"/api/v1/intake-workflow/work-items/{item_id}/candidates",
                json={"recognized": {"p0001": {"title": "三体", "authors": ["刘慈欣"],
                                               "isbn": ISBN_HUOZHE}}},
                headers=headers)
    client.post(f"/api/v1/intake-workflow/work-items/{item_id}/preview-matches",
                headers=headers)
    cand_id = client.get(f"/api/v1/intake-workflow/work-items/{item_id}").json()[
        "data"]["candidates"][0]["id"]
    # 无解决 → 400 且说明人工路径
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/confirm",
                    json={"candidate_ids": [cand_id]}, headers=headers)
    assert r.status_code == 400
    assert "ISBN 归属冲突" in r.text and "force_link" in r.text
    # 显式 force_link → 200，执行后关联既有书
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/confirm",
                    json={"candidate_ids": [cand_id],
                          "resolutions": {str(cand_id): "force_link"}}, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/execute", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["executions"][0]["book_id"] == existing_id
    assert db_session.scalar(select(func.count()).select_from(Book)) == 1


# ── BUG-255：照片图片端点 Owner-only ──

def _member_client(db_session) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, client=("127.0.0.1", 50000))


def test_photo_image_owner_only(client, db_session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", b"owner-photo", "image/png"))],
                    data={"photo_ids": "p0001"}, headers=headers)
    photo_row_id = r.json()["data"]["added"][0]["id"]
    url = f"/api/v1/intake-workflow/photos/{photo_row_id}/image"

    # Owner 可读
    r = client.get(url)
    assert r.status_code == 200 and r.content == b"owner-photo"

    # 匿名 401
    from fastapi.testclient import TestClient as _TC
    anon = _TC(app)
    assert anon.get(url).status_code == 401

    # Member 403（此前缺陷：require_auth 放行 200）
    member = Member(name="普通成员", role="member")
    db_session.add(member)
    db_session.commit()
    agent_access.set_member_password(db_session, member, "member-pass-12345")
    mc = _member_client(db_session)
    login = mc.post("/auth/login",
                    json={"username": member.username, "password": "member-pass-12345"})
    assert login.status_code == 200, login.text
    assert mc.get(url).status_code == 403
    app.dependency_overrides.clear()


# ── BUG-256：批内停用/撤权阻断执行 ──

def _mk_owner(db_engine) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        owner = Member(name="批次 Owner", role="owner")
        s.add(owner)
        s.commit()
        return owner.id


def _prepare_two_command_batch(db_engine, owner_id):
    """两个不同书名的候选 → 两条 create_book 命令。"""
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
        wf.confirm_candidates(s, item_id, [c.id for c in cands],
                              decided_by_member_id=owner_id)
        exec_ids = sorted(e.id for e in s.scalars(select(IntakeCommandExecution)))
    return item_id, exec_ids


def test_disabled_owner_blocked_before_any_side_effect(db_engine):
    owner_id = _mk_owner(db_engine)
    item_id, _ = _prepare_two_command_batch(db_engine, owner_id)
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        owner = s.get(Member, owner_id)
        owner.disabled_at = wf._now()
        s.commit()
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        with pytest.raises(WorkflowAuthError, match="已停用"):
            wf.execute_work_item(s, item_id, requester_member_id=owner_id)
        assert s.scalar(select(func.count()).select_from(Book)) == 0
        item = wf.get_work_item(s, item_id)
        assert item.status == "confirmed"  # 任务退回，未滞留 executing


def test_owner_disabled_between_commands_blocks_remaining(db_engine):
    owner_id = _mk_owner(db_engine)
    item_id, exec_ids = _prepare_two_command_batch(db_engine, owner_id)
    SessionLocal = _session(db_engine)
    real_run = wf.run_command_execution
    calls = {"n": 0}

    def guarded_run(db, execution, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            # 第一条完成后、第二条副作用前停用 Owner（批中撤权）
            member = db.get(Member, owner_id)
            member.disabled_at = wf._now()
            db.commit()
        return real_run(db, execution, **kw)

    with SessionLocal() as s, \
            patch.object(wf, "run_command_execution", side_effect=guarded_run), \
            patch("app.services.intake.fetch_metadata", return_value=None):
        with pytest.raises(WorkflowAuthError, match="已停用"):
            wf.execute_work_item(s, item_id, requester_member_id=owner_id)
        # 第一条已完成；第二条被阻断：无副作用、无回执
        books = s.scalars(select(Book)).fetchall()
        assert len(books) == 1 and books[0].title == "三体"
        exec2 = s.get(IntakeCommandExecution, exec_ids[1])
        assert exec2.status == "pending" and exec2.book_id is None
        item = wf.get_work_item(s, item_id)
        assert item.status == "confirmed"


def test_owner_disabled_after_atomic_commit_blocks_next_command(db_engine):
    """书目与回执提交后停用：保留完成记录，阻断批内后续命令。"""
    owner_id = _mk_owner(db_engine)
    item_id, exec_ids = _prepare_two_command_batch(db_engine, owner_id)
    SessionLocal = _session(db_engine)
    real_create = wf._execute_create_book

    def disabling_create(db, cand, **kwargs):
        result = real_create(db, cand, **kwargs)
        member = db.get(Member, owner_id)
        member.disabled_at = wf._now()  # 首条书目和回执已经一起提交
        db.commit()
        return result

    with SessionLocal() as s, \
            patch.object(wf, "_execute_create_book", side_effect=disabling_create), \
            patch("app.services.intake.fetch_metadata", return_value=None):
        with pytest.raises(WorkflowAuthError):
            wf.execute_work_item(s, item_id, requester_member_id=owner_id)
        books = s.scalars(select(Book)).fetchall()
        assert len(books) == 1
        exec1 = s.get(IntakeCommandExecution, exec_ids[0])
        assert exec1.status == "completed" and exec1.book_id == books[0].id
        assert exec1.lease_owner is None
        first_attempts = exec1.attempts
        assert s.get(IntakeCommandExecution, exec_ids[1]).status == "pending"

    # 重新授权后执行：首条重放已完成回执，仅第二条新建
    with SessionLocal() as s:
        owner = s.get(Member, owner_id)
        owner.disabled_at = None
        s.commit()
    with SessionLocal() as s, patch("app.services.intake.fetch_metadata", return_value=None):
        summary = wf.execute_work_item(s, item_id, requester_member_id=owner_id)
        assert summary["status"] == "completed"
        exec1 = s.get(IntakeCommandExecution, exec_ids[0])
        assert exec1.status == "completed" and exec1.book_id == books[0].id
        assert exec1.attempts == first_attempts
        # 活着为第二条正常新建 → 共 2 本
        assert s.scalar(select(func.count()).select_from(Book)) == 2


def test_disabled_owner_cannot_execute_via_api(client, db_session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"},
                    headers=headers)
    item_id = r.json()["data"]["id"]
    client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                files=[("files", ("a.png", _png_bytes((1, 1, 1)), "image/png")),
                       ("files", ("b.png", _png_bytes((2, 2, 2)), "image/png"))],
                data={"photo_ids": "p0001,p0002"}, headers=headers)
    client.post(f"/api/v1/intake-workflow/work-items/{item_id}/candidates",
                json={"recognized": {
                    "p0001": {"title": "三体", "authors": ["刘慈欣"]},
                    "p0002": {"title": "活着", "authors": ["余华"]}}},
                headers=headers)
    snapshot = client.get(f"/api/v1/intake-workflow/work-items/{item_id}").json()["data"]
    cand_ids = [c["id"] for c in snapshot["candidates"]]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/confirm",
                    json={"candidate_ids": cand_ids}, headers=headers)
    assert r.status_code == 200, r.text

    # 停用 Owner 后执行必须被拒（require_owner 即时失效），命令不得入库
    owner = db_session.scalars(select(Member).where(Member.role == "owner")).first()
    owner.disabled_at = wf._now()
    db_session.commit()
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/execute", headers=headers)
    assert r.status_code in (401, 403)
    assert db_session.scalar(select(func.count()).select_from(Book)) == 0
