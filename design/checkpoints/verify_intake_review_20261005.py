"""独立执行四项修复的验收；某项失败不阻断其余项目。

从仓库根运行：backend/.venv/bin/python design/checkpoints/verify_intake_review_20261005.py
原始 repro_intake_review_20261005.py 保持不动。本脚本仅用临时 SQLite、
合成图片、虚构密钥和模拟网络；返回 1 表示仍有验收失败。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "backend"))
    with tempfile.TemporaryDirectory(prefix="bookshelf-acceptance-20261005-") as directory:
        root = Path(directory)
        os.environ["DATABASE_URL"] = f"sqlite:///{root / 'verify.db'}"
        os.environ["DATA_DIR"] = str(root / "data")
        from PIL import Image
        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker
        from app.config import settings
        from app.models import IntakeCandidate
        from app.models.base import Base, create_engine_from_url
        from app.services import intake_workflow as wf
        from app.services.metadata import http
        from app.services.vision import VisionServiceResult

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine_from_url(os.environ["DATABASE_URL"])
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, autoflush=False)

        def item_with_photos(count=1):
            with sessions() as db:
                item = wf.create_work_item(db)
                photos = []
                for i in range(1, count + 1):
                    output = io.BytesIO()
                    Image.new("RGB", (2, 2), (i, i, i)).save(output, format="PNG")
                    photos.append({"photo_id": f"p{i}", "filename": "cover.png",
                                   "role": "cover", "content": output.getvalue()})
                wf.add_photos(db, item.id, photos)
                return item.id

        def bug288():
            url = "https://example.invalid/books?api_key=synthetic%2Bsecret%2F2026%3D&q=isbn"
            # 首项对应原复现，后三项分别改变编码十六进制大小写；解码值相同。
            variants = ("synthetic%2Bsecret%2F2026%3D", "synthetic%2bsecret%2f2026%3d",
                        "synthetic%2bsecret%2F2026%3D", "synthetic+secret/2026=")
            leaked = []
            for secret in variants:
                events = []
                http.clear_response_listeners()
                http.add_response_listener(events.append)
                try:
                    echoed = "https://example.invalid/books?api_key=" + secret + "&q=isbn"
                    with patch.object(http.urllib.request, "urlopen", side_effect=
                                      urllib.error.URLError("failed request " + echoed)):
                        assert http.get_json(url) is None
                    assert "api_key=***" in events[0]["url"]
                    assert "q=isbn" in events[0]["error"]
                    if secret in events[0]["error"]:
                        leaked.append(secret)
                finally:
                    http.clear_response_listeners()
            assert not leaked, f"诊断事件 error 仍泄露可还原的编码密钥：{leaked}"

        def bug289():
            with sessions() as db:
                item = wf.create_work_item(db)
                cand = IntakeCandidate(work_item_id=item.id, title="恢复探针",
                                       status="pending_review", photo_ids="[]")
                db.add(cand)
                db.commit()
                command = wf.confirm_candidates(db, item.id, [cand.id],
                                                decided_by_member_id=None)[0]
                command.status = "executing"
                command.lease_owner = "crashed-worker"
                command.lease_expires_at = wf._now() + timedelta(seconds=30)
                item.status = "executing"
                db.commit()
                result = wf.execute_work_item(db, item.id)
                db.refresh(command)
                assert result["status"] == command.status == "executing"
                command.lease_expires_at = wf._now() - timedelta(seconds=1)
                db.commit()
                with patch("app.services.intake.fetch_metadata", return_value=None):
                    result = wf.execute_work_item(db, item.id)
                assert result["status"] == "completed"

        def bug286():
            item_id = item_with_photos(2)
            with sessions() as db:
                cand = wf.build_candidates(db, item_id, {
                    "p1": wf.RecognitionInput(title="模型初稿"),
                    "p2": wf.RecognitionInput(title="模型初稿"),
                })[0]
                wf.update_candidate(db, cand.id, title="人工修正书名", authors=["人工作者"])
                _, split = wf.split_candidate(db, cand.id, photo_ids_to_new=["p2"])
                assert split.version == 1
                assert json.loads(split.evidence)["title"]["source"] == "user"
                with patch("app.services.vision.recognize_cover_fields", return_value=
                           VisionServiceResult(ok=False, error_code="timeout", message="模型超时")):
                    wf.recognize_photos(db, item_id)
                db.expire_all()
                live = list(db.scalars(select(IntakeCandidate).where(
                    IntakeCandidate.work_item_id == item_id,
                    IntakeCandidate.status == "pending_review")))
                assert len(live) == 2
                assert all(c.title == "人工修正书名" for c in live)
                assert {tuple(json.loads(c.photo_ids)) for c in live} == {("p1",), ("p2",)}

        def bug285():
            item_id = item_with_photos()
            with sessions() as db:
                old_id = wf.build_candidates(db, item_id, {
                    "p1": wf.RecognitionInput(title="并发书甲")})[0].id
            started = threading.Event()
            attempting_lock = threading.Event()
            finished = threading.Event()
            outcomes = []
            errors = []
            real_lock = wf._lock_work_item

            def observed_lock(db, wid):
                if threading.current_thread() is thread:
                    attempting_lock.set()
                return real_lock(db, wid)

            def confirm_old():
                try:
                    with sessions() as other:
                        started.set()
                        wf.confirm_candidates(other, item_id, [old_id], decided_by_member_id=None)
                    outcomes.append("unexpected_confirm")
                except wf.WorkflowError:
                    outcomes.append("stale_candidate_rejected")
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    finished.set()

            thread = threading.Thread(target=confirm_old)
            with sessions() as db, patch.object(wf, "_lock_work_item", side_effect=observed_lock):
                real_scalars = db.scalars
                calls = 0

                def interleaved(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 3:
                        thread.start()
                        assert started.wait(5) and attempting_lock.wait(5)
                        assert not finished.wait(0.2), "确认没有等待重建释放任务锁"
                    return real_scalars(*args, **kwargs)

                with patch.object(db, "scalars", side_effect=interleaved):
                    new_id = wf.build_candidates(db, item_id, {
                        "p1": wf.RecognitionInput(title="并发书丙")})[0].id
                thread.join(10)
            assert not thread.is_alive(), "确认线程未结束"
            assert not errors, errors
            assert outcomes == ["stale_candidate_rejected"], outcomes
            with sessions() as db:
                wf.confirm_candidates(db, item_id, [new_id], decided_by_member_id=None)
                with patch("app.services.intake.fetch_metadata", return_value=None):
                    result = wf.execute_work_item(db, item_id)
                assert result["status"] == "completed"
                assert len(result["executions"]) == 1
                assert result["executions"][0]["result"]["photo_ids"] == ["p1"]

        failures = 0
        try:
            for name, verify in (("BUG-288", bug288), ("BUG-289", bug289),
                                 ("BUG-286", bug286), ("BUG-285", bug285)):
                try:
                    verify()
                    print(name, "PASS")
                except Exception as exc:
                    failures += 1
                    print(name, "FAIL", type(exc).__name__, str(exc))
        finally:
            engine.dispose()
        return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())
