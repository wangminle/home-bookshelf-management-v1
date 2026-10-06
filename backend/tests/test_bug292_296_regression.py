"""CHK-122 复核（2026-10-05）五项残留缺陷回归：BUG-292/293/294/295/296。

- BUG-292：只拆分、未改字段的候选（版本因拆分变为 2，但证据来源仍是 vision）
  不算人工候选，重新识别应正常取代，不再保住拆分前的错误书名；
- BUG-293：拆分不得把全部照片拆走（原候选将没有照片）；
- BUG-294：已确认（未执行）候选不可拆分，避免旧命令失格把任务打成 failed；
- BUG-295：重建时 recognized 未提及的照片保留既有警告，不按空结果清空；
- BUG-296：诊断事件的 error 字段对再编码（% 反复编成 %25，含三层及以上、
  内层十六进制小写/混写）的密钥回显同样脱敏。
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import IntakeCandidate, IntakePhoto
from app.services import intake_workflow as wf
from app.services.metadata import http as metadata_http


def _session(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _item_with_photos(db_engine, n: int = 2) -> int:
    SessionLocal = _session(db_engine)
    with SessionLocal() as s:
        item = wf.create_work_item(s, title="BUG-292~296 回归")
        photos = []
        for i in range(1, n + 1):
            image = io.BytesIO()
            Image.new("RGB", (2, 2), (i, i, i)).save(image, format="PNG")
            photos.append({"photo_id": f"p{i}", "filename": f"{i}.png",
                           "content": image.getvalue(), "role": "cover"})
        wf.add_photos(s, item.id, photos)
        return item.id


def _candidates(s, item_id: int) -> list[IntakeCandidate]:
    return list(s.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == item_id,
        IntakeCandidate.status == "pending_review")))


# ── BUG-292 ──

def test_split_only_candidate_is_rebuilt_on_recognize(db_engine):
    """只拆分未改字段：重新识别时原候选被新识别结果取代，不保住错误书名。"""
    SessionLocal = _session(db_engine)
    item_id = _item_with_photos(db_engine, 2)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="错误共用书名"),
            "p2": wf.RecognitionInput(title="错误共用书名"),
        })[0]
        original, split = wf.split_candidate(s, cand.id, photo_ids_to_new=["p2"])
        assert original.version == 2 and split.version == 1
        # 拆分后两个候选的证据来源都仍是 vision（无人工编辑）
        for c in (original, split):
            evidence = json.loads(c.evidence)
            assert all(v["source"] == "vision" for v in evidence.values())

        # 重新识别：两张照片给出各自正确书名（protect_edited 与页面“模型识别”一致）
        rebuilt = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="正确书甲", source="vision"),
            "p2": wf.RecognitionInput(title="正确书乙", source="vision"),
        }, protect_edited=True)
        titles = sorted(c.title for c in rebuilt)
        assert titles == ["正确书乙", "正确书甲"]
        live = _candidates(s, item_id)
        assert all(c.title != "错误共用书名" for c in live)


def test_split_after_user_edit_still_protected(db_engine):
    """对照（BUG-286 不回退）：先人工编辑再拆分的候选，重新识别仍被保护。"""
    SessionLocal = _session(db_engine)
    item_id = _item_with_photos(db_engine, 2)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="模型初稿"),
            "p2": wf.RecognitionInput(title="模型初稿"),
        })[0]
        wf.update_candidate(s, cand.id, title="人工修正书名")
        original, split = wf.split_candidate(s, cand.id, photo_ids_to_new=["p2"])
        wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="识别新书甲", source="vision"),
            "p2": wf.RecognitionInput(title="识别新书乙", source="vision"),
        }, protect_edited=True)
        s.expire_all()
        live = _candidates(s, item_id)
        assert {c.id for c in live} == {original.id, split.id}
        assert all(c.title == "人工修正书名" for c in live)


# ── BUG-293 ──

def test_split_rejects_moving_all_photos(db_engine):
    """拆走全部照片会留下无照片空壳，必须拒绝且原候选不变。"""
    SessionLocal = _session(db_engine)
    item_id = _item_with_photos(db_engine, 2)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="书甲"),
            "p2": wf.RecognitionInput(title="书甲"),
        })[0]
        with pytest.raises(wf.WorkflowError, match="全部照片"):
            wf.split_candidate(s, cand.id, photo_ids_to_new=["p1", "p2"])
        s.expire_all()
        kept = s.get(IntakeCandidate, cand.id)
        assert json.loads(kept.photo_ids) == ["p1", "p2"]
        assert kept.version == 1 and kept.status == "pending_review"


def test_split_rejects_single_photo_candidate(db_engine):
    """单照片候选的唯一拆法就是拆空，同样拒绝。"""
    SessionLocal = _session(db_engine)
    item_id = _item_with_photos(db_engine, 1)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="书甲"),
        })[0]
        with pytest.raises(wf.WorkflowError, match="全部照片"):
            wf.split_candidate(s, cand.id, photo_ids_to_new=["p1"])


# ── BUG-294 ──

def test_split_rejects_confirmed_candidate(db_engine):
    """已确认候选不可拆分；拒绝后原确认仍然有效，可直接执行完成。"""
    SessionLocal = _session(db_engine)
    item_id = _item_with_photos(db_engine, 2)
    with SessionLocal() as s:
        cand = wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="书甲"),
            "p2": wf.RecognitionInput(title="书甲"),
        })[0]
        wf.confirm_candidates(s, item_id, [cand.id], decided_by_member_id=None)
        with pytest.raises(wf.WorkflowError) as excinfo:
            wf.split_candidate(s, cand.id, photo_ids_to_new=["p2"])
        message = str(excinfo.value)
        assert "已确认" in message
        # 指引必须是可行路径：保存编辑使候选退回待核对后再拆分——
        # 重新识别重建会保留已确认候选，指去重新识别是死路
        assert "待核对" in message and "重新识别" not in message
        s.expire_all()
        kept = s.get(IntakeCandidate, cand.id)
        assert kept.status == "confirmed" and kept.version == 1
        assert json.loads(kept.photo_ids) == ["p1", "p2"]
        with patch("app.services.intake.fetch_metadata", return_value=None):
            result = wf.execute_work_item(s, item_id)
        assert result["status"] == "completed"


# ── BUG-295 ──

def test_rebuild_keeps_warnings_of_unrecognized_photos(db_engine):
    """局部重建只更新 recognized 提及照片的警告，未提及照片保留既有警告。"""
    SessionLocal = _session(db_engine)
    item_id = _item_with_photos(db_engine, 2)
    blur = [{"code": "blur", "message": "照片模糊"}]
    with SessionLocal() as s:
        wf.build_candidates(s, item_id, {
            "p1": wf.RecognitionInput(title="书甲", warnings=blur),
            "p2": wf.RecognitionInput(title="书乙", warnings=blur),
        })
        photos = {p.photo_id: p for p in s.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == item_id))}
        assert json.loads(photos["p1"].warnings)[0]["code"] == "blur"

        # 只提交 p2 的识别结果重建（公开的候选构建接口允许部分输入）
        wf.build_candidates(s, item_id, {
            "p2": wf.RecognitionInput(title="书乙", warnings=[]),
        })
        s.expire_all()
        photos = {p.photo_id: p for p in s.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == item_id))}
        assert json.loads(photos["p1"].warnings)[0]["code"] == "blur"  # 保留
        assert json.loads(photos["p2"].warnings) == []                 # 本轮成功清除


# ── BUG-296 ──

@pytest.mark.parametrize("echoed_secret", [
    "synthetic%252Bsecret%252F2026%253D",     # 二次编码（% → %25）
    "synthetic%252bsecret%252f2026%253d",     # 二次编码内层十六进制小写
    "synthetic%252Bsecret%252f2026%253D",     # 二次编码内层十六进制大小写混用
    "synthetic%25252Bsecret%25252F2026%253D",   # 三层编码（% → %2525）
    "synthetic%25252bsecret%25252f2026%25253d", # 三层编码内层十六进制小写
    "synthetic%25252Bsecret%25252f2026%253D",   # 三层编码内层十六进制大小写混用
    "synthetic%2Bsecret%2F2026%3D",         # 单层编码（BUG-288 口径不回退）
    "synthetic%2bsecret%2f2026%3d",         # 单层编码小写十六进制
    "synthetic+secret/2026=",               # 明文解码写法
])
def test_error_message_masks_reencoded_secret(echoed_secret):
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
    for leaked in ("synthetic%25252Bsecret", "secret%25252F2026",
                   "synthetic%25252bsecret", "secret%25252f2026",
                   "synthetic%252Bsecret", "secret%252F2026",
                   "synthetic%252bsecret", "secret%252f2026",
                   "synthetic%2Bsecret", "secret%2F2026", "synthetic+secret/2026="):
        assert leaked not in blob
    assert "api_key=***" in events[0]["url"]
    # 非敏感查询参数保留，便于诊断
    assert "q=isbn" in blob
