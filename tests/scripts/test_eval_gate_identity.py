"""BI-10 回归：--yes 门控证据身份核验（规划 §4.7）。

规则：换模型/提示词/任务类型后旧结果不通用；样本未核验、样本不足或证据
不匹配时拒绝自动确认。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import batch_import_covers as bic  # noqa: E402
import batch_manifest as bm  # noqa: E402


def make_covers_local(tmp_path, names):
    d = tmp_path / "covers"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"\xff\xd8fake-jpg")
    return d


def entry(file, status="pending", **kw):
    base = {"photo_id": None, "file": file, "sha256": None, "role": "cover",
            "title": None, "author": None, "isbn": None, "price": None,
            "channel": None, "location": None, "member_id": None,
            "confirmed_fields": [], "status": status, "note": None,
            "warnings": [], "candidate_id": None, "result": None}
    base.update(kw)
    return base


def write_manifest(path, source_dir, entries):
    path.write_text(json.dumps(
        {"version": 2, "batch_id": "b", "source_dir": str(source_dir),
         "created_at": "t", "updated_at": "t", "entries": entries}), encoding="utf-8")
    return path


def full_result(*, accuracy=0.9, verified_accuracy=0.85, verified_groups=25,
                completed_verified_groups=None, task="cover_title_author",
                prompt="vision-cover-v1", fingerprint="fp-current", golden="gs-current",
                planned=20, completed=None, complete=True):
    """BI-10/BUG-257-259 后的完整证据：身份 + 完成度 + 门控口径准确率。"""
    if completed is None:
        completed = planned
    if completed_verified_groups is None:
        completed_verified_groups = min(verified_groups, completed)
    data = {"generated_at": "t", "metrics": {"book_level_accuracy": accuracy,
                                             "book_level_accuracy_verified": verified_accuracy},
            "identity": {"task_type": task, "prompt_version": prompt,
                         "model_fingerprint": fingerprint, "golden_sha256": golden},
            "sample": {"verified_groups": verified_groups},
            "progress": {"planned_photos": planned, "completed_photos": completed,
                         "planned_groups": planned, "completed_groups": completed,
                         "completed_verified_groups": completed_verified_groups,
                         "complete": complete, "smoke": planned < 20}}
    return data


GATE_ARGS = ["--gate-task-type", "cover_title_author", "--gate-prompt-version", "vision-cover-v1",
             "--gate-model-fingerprint", "fp-current", "--gate-golden-sha", "gs-current"]


# ── 纯函数核验 ──

def test_full_evidence_passes():
    ok, msg = bm.verify_eval_evidence(full_result())
    assert ok, msg


def test_legacy_result_without_identity_rejected():
    ok, msg = bm.verify_eval_evidence({"generated_at": "t",
                                       "metrics": {"book_level_accuracy": 0.95}})
    assert not ok and "不通用" in msg


def test_task_type_mismatch_rejected():
    ok, msg = bm.verify_eval_evidence(full_result(task="cover_isbn_only"))
    assert not ok and "任务类型" in msg


def test_model_fingerprint_mismatch_rejected():
    ok, msg = bm.verify_eval_evidence(full_result(), expected_model_fingerprint="fp-new")
    assert not ok and "模型配置指纹" in msg and "旧证据不通用" in msg


def test_prompt_version_mismatch_rejected():
    ok, msg = bm.verify_eval_evidence(full_result(), expected_prompt_version="vision-cover-v2")
    assert not ok and "提示词版本" in msg


def test_golden_sha_mismatch_rejected():
    ok, msg = bm.verify_eval_evidence(full_result(), expected_golden_sha="gs-new")
    assert not ok and "测试集版本" in msg


def test_matching_expectations_pass():
    ok, _ = bm.verify_eval_evidence(
        full_result(), expected_model_fingerprint="fp-current",
        expected_prompt_version="vision-cover-v1", expected_golden_sha="gs-current")
    assert ok


def test_unverified_or_insufficient_sample_rejected():
    ok, msg = bm.verify_eval_evidence(full_result(verified_groups=19))
    assert not ok and "低于下限" in msg
    ok2, msg2 = bm.verify_eval_evidence(full_result(verified_groups=0))
    assert not ok2 and "未核验" in msg2
    ok3, _ = bm.verify_eval_evidence(full_result(verified_groups=20))
    assert ok3  # 恰好达到下限


def test_missing_identity_field_rejected():
    result = full_result()
    del result["identity"]["model_fingerprint"]
    ok, msg = bm.verify_eval_evidence(result)
    assert not ok and "identity.model_fingerprint" in msg


# ── 与批量脚本的集成 ──

def _write_result_file(eval_dir: Path, data: dict) -> None:
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "results-20261002-090000.json").write_text(json.dumps(data), encoding="utf-8")


class FakeClient:
    def __init__(self):
        self.calls = []

    def health(self):
        return {"status": "ok"}

    def add(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "data": {"already_exists": False, "book": {"id": 1},
                                     "action": "created_new", "message": "ok"}}


def _run_yes(tmp_path, eval_data, *extra):
    covers = make_covers_local(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers,
                              [entry("a.jpg", "recognized", title="三体")])
    eval_dir = tmp_path / "eval"
    _write_result_file(eval_dir, eval_data)
    fake = FakeClient()
    rc = bic.cmd_run(
        bic.build_parser().parse_args(
            ["run", "--manifest", str(manifest), "--report", str(tmp_path / "r.json"),
             "--yes", "--eval-dir", str(eval_dir)] + list(extra)),
        client=fake)
    return rc, fake


# ── BUG-257/258/259 回归：身份逐一比对、完成度、门控口径准确率 ──

def test_run_yes_with_verified_identity_passes(tmp_path):
    rc, fake = _run_yes(tmp_path, full_result(accuracy=0.9, verified_groups=21), *GATE_ARGS)
    assert rc == 0 and len(fake.calls) == 1


def test_run_yes_blocked_when_sample_below_floor(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, full_result(accuracy=0.9, verified_groups=3,
                                       completed_verified_groups=3), *GATE_ARGS)
    assert "低于下限" in capsys.readouterr().err
    saved = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert saved["entries"][0]["status"] == "recognized"  # 未放行


def test_run_yes_blocked_without_identity(tmp_path, capsys):
    """无 identity 的旧格式证据：即使指标/完成度齐全，也按'不通用'拒绝。"""
    data = {"generated_at": "t",
            "metrics": {"book_level_accuracy": 0.99, "book_level_accuracy_verified": 0.99},
            "progress": {"planned_photos": 20, "completed_photos": 20,
                         "completed_verified_groups": 21, "complete": True, "smoke": False}}
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, data, *GATE_ARGS)
    assert "不通用" in capsys.readouterr().err


def test_run_yes_fingerprint_arg_blocks_stale_evidence(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, full_result(fingerprint="fp-old"), *GATE_ARGS)
    assert "旧证据不通用" in capsys.readouterr().err


def test_run_yes_fingerprint_arg_matching_passes(tmp_path):
    args = [a if a != "fp-current" else "fp-new" for a in GATE_ARGS]
    rc, fake = _run_yes(tmp_path, full_result(fingerprint="fp-new"), *args)
    assert rc == 0 and len(fake.calls) == 1


def test_run_yes_rejected_without_gate_args(tmp_path, capsys):
    """BUG-257：四个期望身份缺一不可；不允许默认 --yes 放行身份未绑定的报告。"""
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, full_result())
    err = capsys.readouterr().err
    assert "逐一比对" in err and "--gate-task-type" in err


def test_run_yes_rejected_when_gate_arg_missing_one(tmp_path, capsys):
    """BUG-257：只传部分身份同样拒绝（如缺 --gate-golden-sha）。"""
    partial = GATE_ARGS[:6]  # 缺 --gate-golden-sha
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, full_result(), *partial)
    assert "测试集版本" in capsys.readouterr().err


def test_run_yes_rejected_when_prompt_changed(tmp_path, capsys):
    """BUG-257：提示词版本与本次实际使用不符 → 拒绝旧证据。"""
    args = [a if a != "vision-cover-v1" else "vision-cover-v2" for a in GATE_ARGS]
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, full_result(), *args)
    assert "提示词版本" in capsys.readouterr().err


def test_run_yes_rejected_when_golden_changed(tmp_path, capsys):
    """BUG-257：测试集版本与本次实际使用不符 → 拒绝旧证据。"""
    args = [a if a != "gs-current" else "gs-new" for a in GATE_ARGS]
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, full_result(), *args)
    assert "测试集版本" in capsys.readouterr().err


def test_run_yes_rejected_when_task_type_changed(tmp_path, capsys):
    args = [a if a != "cover_title_author" else "cover_isbn_only" for a in GATE_ARGS]
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, full_result(), *args)
    assert "任务类型" in capsys.readouterr().err


def test_run_yes_blocked_for_incomplete_smoke_report(tmp_path, capsys):
    """BUG-258：limit 截断的冒烟报告（plan 20 只完成 1）禁止作为自动确认依据，
    即使全集口径已核验 20 本、准确率 100%。"""
    data = full_result(accuracy=1.0, verified_accuracy=1.0, verified_groups=20,
                       planned=20, completed=1, completed_verified_groups=1)
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, data, *GATE_ARGS)
    assert "未完成" in capsys.readouterr().err


def test_run_yes_blocked_for_partial_report_without_progress(tmp_path, capsys):
    """BUG-258：中断恢复的旧格式报告无 progress 完成度信息 → 拒绝。"""
    data = full_result()
    del data["progress"]
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, data, *GATE_ARGS)
    assert "progress" in capsys.readouterr().err


def test_run_yes_gate_uses_verified_caliber_not_diluted(tmp_path, capsys):
    """BUG-259：20 本已核验全错 + 80 条未核验全对时，全集口径 0.8 不能过门控——
    门控准确率仅由已完成且人工核验的不同书计算。"""
    data = full_result(accuracy=0.8, verified_accuracy=0.0, verified_groups=20)
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, data, *GATE_ARGS)
    err = capsys.readouterr().err
    assert "0.0%" in err and "仅已核验样本" in err


def test_run_yes_blocked_without_verified_caliber_metric(tmp_path, capsys):
    """BUG-259：缺门控口径指标（旧格式 metrics）→ 拒绝，不回落到全集口径。"""
    data = full_result(accuracy=0.95)
    del data["metrics"]["book_level_accuracy_verified"]
    with pytest.raises(SystemExit):
        _run_yes(tmp_path, data, *GATE_ARGS)
    assert "book_level_accuracy_verified" in capsys.readouterr().err
