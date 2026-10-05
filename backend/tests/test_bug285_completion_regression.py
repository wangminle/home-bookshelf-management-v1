"""BUG-285：归属复验之后的并发窗口，以及同批未落库的照片归属。"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import IntakeCandidate, IntakeChangeSet, IntakeCommandExecution, IntakeDecision
from app.services import intake_workflow as wf


def _fixture(db_engine):
    sessions = sessionmaker(bind=db_engine, autoflush=False)
    with sessions() as db:
        item = wf.create_work_item(db)
        wf.add_photos(db, item.id, [
            {"photo_id": pid, "filename": pid + ".jpg", "content": pid.encode()}
            for pid in ("p1", "p2")
        ])
        candidates = wf.build_candidates(db, item.id, {
            "p1": wf.RecognitionInput(title="书甲"),
            "p2": wf.RecognitionInput(title="书乙"),
        })
        return sessions, item.id, {c.title: c.id for c in candidates}


@pytest.mark.parametrize("read_number", [3, 4], ids=["before-recheck", "after-recheck"])
def test_rebuild_and_confirm_never_create_duplicate_photo_owners(db_engine, read_number):
    sessions, item_id, ids = _fixture(db_engine)
    started, finished = threading.Event(), threading.Event()
    errors = []

    def confirm_old():
        try:
            with sessions() as db:
                started.set()
                wf.confirm_candidates(db, item_id, [ids["书甲"]], decided_by_member_id=None)
        except wf.WorkflowError:
            pass  # 重建先提交时，旧候选确认应拒绝。
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=confirm_old)
    try:
        with sessions() as db:
            real_scalars = db.scalars
            calls = 0

            def interleave(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == read_number:
                    thread.start()
                    assert started.wait(5)
                    finished.wait(0.2)  # 无锁时让确认提交；有锁时确认等待重建。
                return real_scalars(*args, **kwargs)

            with patch.object(db, "scalars", side_effect=interleave):
                try:
                    wf.build_candidates(db, item_id, {
                        "p1": wf.RecognitionInput(title="重建书丙"),
                        "p2": wf.RecognitionInput(title="书乙"),
                    })
                except wf.WorkflowError:
                    db.rollback()  # 确认先提交时，重建可拒绝。
    finally:
        if thread.ident is not None:
            thread.join(10)
    assert not thread.is_alive()
    assert not errors, errors
    with sessions() as db:
        owners = [c for c in db.scalars(select(IntakeCandidate).where(
            IntakeCandidate.work_item_id == item_id,
            IntakeCandidate.status != "rejected")) if "p1" in json.loads(c.photo_ids)]
        assert len(owners) == 1, [(c.id, c.title, c.status) for c in owners]


def test_one_confirmation_batch_rejects_overlapping_photos_atomically(db_engine):
    sessions, item_id, ids = _fixture(db_engine)
    with sessions() as db:
        wf.update_candidate(db, ids["书乙"], photo_ids=["p1"], title="同图新书乙")
        with pytest.raises(wf.WorkflowError, match="归属冲突"):
            wf.confirm_candidates(db, item_id, [ids["书甲"], ids["书乙"]],
                                  decided_by_member_id=None)
        # 调用方即使继续提交，也不应留下半批确认或任何可执行命令。
        db.commit()
        assert {c.status for c in db.scalars(select(IntakeCandidate).where(
            IntakeCandidate.work_item_id == item_id))} == {"pending_review"}
        assert not list(db.scalars(select(IntakeChangeSet)))
        assert not list(db.scalars(select(IntakeDecision)))
        assert not list(db.scalars(select(IntakeCommandExecution)))
