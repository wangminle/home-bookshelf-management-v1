"""复现 2026-10-05 修复复核中的四个残口。

从仓库根运行：backend/.venv/bin/python design/checkpoints/repro_intake_review_20261005.py
仅使用临时数据库、合成图片及虚构密钥，不访问外部服务或生产数据库。
这里的断言证明审查时缺陷仍存在；修复后断言失败是预期结果。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "backend"))
    with tempfile.TemporaryDirectory(prefix="bookshelf-review-20261005-") as directory:
        root = Path(directory)
        os.environ["DATABASE_URL"] = f"sqlite:///{root / 'review.db'}"
        os.environ["DATA_DIR"] = str(root / "data")

        from PIL import Image
        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker

        from app.config import settings
        from app.models import Book, IntakeCandidate
        from app.models.base import Base, create_engine_from_url
        from app.services import intake_workflow as wf
        from app.services.metadata import http
        from app.services.vision import VisionServiceResult

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine_from_url(os.environ["DATABASE_URL"])
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, autoflush=False)

        def png(color):
            output = io.BytesIO()
            Image.new("RGB", (2, 2), color).save(output, format="PNG")
            return output.getvalue()

        def item_with_photos(count=1):
            with sessions() as db:
                item = wf.create_work_item(db)
                wf.add_photos(db, item.id, [
                    {"photo_id": f"p{i}", "filename": "cover.png", "role": "cover",
                     "content": png((i, i, i))}
                    for i in range(1, count + 1)
                ])
                return item.id

        # BUG-288：异常中回显编码后的虚构密钥。
        url = "https://example.invalid/books?api_key=synthetic%2Bsecret%2F2026%3D&q=isbn"
        events = []
        http.clear_response_listeners()
        http.add_response_listener(events.append)
        try:
            with patch.object(http.urllib.request, "urlopen", side_effect=
                              urllib.error.URLError("failed request " + url)):
                assert http.get_json(url) is None
            assert "api_key=***" in events[0]["url"]
            assert "synthetic%2Bsecret%2F2026%3D" in events[0]["error"]
            print("BUG-288", json.dumps(events[0], ensure_ascii=False))
        finally:
            http.clear_response_listeners()

        # BUG-289：中断后租约还剩 30 秒时点击恢复。
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
            assert result["status"] == "failed" and command.status == "executing"
            assert wf._as_aware(command.lease_expires_at) > wf._now()
            print("BUG-289", "任务 failed，命令 executing，租约仍有效")

        # BUG-286：已保存人工编辑后，拆分产生的 v1 候选不被保护。
        item_id = item_with_photos(2)
        with sessions() as db:
            cand = wf.build_candidates(db, item_id, {
                "p1": wf.RecognitionInput(title="模型初稿"),
                "p2": wf.RecognitionInput(title="模型初稿"),
            })[0]
            wf.update_candidate(db, cand.id, title="人工修正书名", authors=["人工修正作者"])
            original, split = wf.split_candidate(db, cand.id, photo_ids_to_new=["p2"])
            assert split.version == 1
            assert json.loads(split.evidence)["title"]["source"] == "user"
            with patch("app.services.vision.recognize_cover_fields", return_value=
                       VisionServiceResult(ok=False, error_code="timeout", message="模型超时")):
                wf.recognize_photos(db, item_id)
            db.expire_all()
            after = list(db.scalars(select(IntakeCandidate).where(
                IntakeCandidate.work_item_id == item_id)))
            p2 = next(c for c in after if json.loads(c.photo_ids) == ["p2"])
            assert p2.title is None
            assert db.get(IntakeCandidate, original.id).title == "人工修正书名"
            print("BUG-286", [(c.id, c.version, c.title, json.loads(c.photo_ids)) for c in after])

        # BUG-285：重建读完归属集合后，由另一真实会话确认旧候选。
        item_id = item_with_photos()
        with sessions() as db:
            old = wf.build_candidates(db, item_id, {"p1": wf.RecognitionInput(title="并发书甲")})[0]
            old_id = old.id
        with sessions() as db:
            real_scalars = db.scalars
            calls = 0

            def interleaved_scalars(*args, **kwargs):
                nonlocal calls
                calls += 1
                # 第1次读照片，第2次读confirmed/executed归属；第3次准备清理pending。
                if calls == 3:
                    with sessions() as other:
                        wf.confirm_candidates(other, item_id, [old_id], decided_by_member_id=None)
                return real_scalars(*args, **kwargs)

            with patch.object(db, "scalars", side_effect=interleaved_scalars):
                new = wf.build_candidates(db, item_id, {
                    "p1": wf.RecognitionInput(title="并发书丙")})[0]
            wf.confirm_candidates(db, item_id, [new.id], decided_by_member_id=None)
            with patch("app.services.intake.fetch_metadata", return_value=None):
                result = wf.execute_work_item(db, item_id)
            receipts = result["executions"]
            assert result["status"] == "completed" and len(receipts) == 2
            assert len({e["book_id"] for e in receipts}) == 2
            assert all(e["result"]["photo_ids"] == ["p1"] for e in receipts)
            books = list(db.scalars(select(Book)))
            print("BUG-285", result["status"], [(b.id, b.title) for b in books],
                  [(e["book_id"], e["result"]["photo_ids"]) for e in receipts])
        engine.dispose()


if __name__ == "__main__":
    main()
