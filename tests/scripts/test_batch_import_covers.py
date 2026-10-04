"""batch_import_covers.py 单元测试：manifest 状态机、scan 合并、run 分类与 eval 门控。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import batch_import_covers as bic  # noqa: E402


# ── 测试工具 ──

def make_covers(tmp_path: Path, names: list[str]) -> Path:
    d = tmp_path / "covers"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"\xff\xd8fake-jpg")
    return d


def entry(file: str | None, status: str = "pending", **kw) -> dict:
    base = {"file": file, "title": None, "author": None, "isbn": None,
            "price": None, "channel": None, "location": None, "member_id": None,
            "status": status, "note": None, "result": None}
    base.update(kw)
    return base


def write_manifest(path: Path, source_dir: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps(
        {"version": 1, "source_dir": str(source_dir), "created_at": "t", "updated_at": "t",
         "entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def created_resp(book_id: int = 1) -> dict:
    return {"ok": True, "data": {"already_exists": False, "book": {"id": book_id},
                                 "action": "created_new", "message": "ok"}}


def exists_resp(book_id: int = 9) -> dict:
    return {"ok": True, "data": {"already_exists": True, "book": {"id": book_id},
                                 "action": "exists", "message": "已存在"}}


class FakeClient:
    """按 isbn/title 键返回预设响应或抛预设异常的假客户端。"""

    def __init__(self, behavior: dict | None = None, health_error: str | None = None):
        self.behavior = behavior or {}
        self.health_error = health_error
        self.calls: list[dict] = []

    def health(self):
        if self.health_error:
            raise RuntimeError(self.health_error)
        return {"status": "ok"}

    def add(self, **kwargs):
        self.calls.append(kwargs)
        key = kwargs.get("isbn") or kwargs.get("title")
        behavior = self.behavior.get(key)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


def run_args(manifest: Path, report: Path, *extra: str, eval_dir: Path | None = None):
    argv = ["run", "--manifest", str(manifest), "--report", str(report)]
    if eval_dir is not None:
        argv += ["--eval-dir", str(eval_dir)]
    return bic.build_parser().parse_args(argv + list(extra))


# ── scan ──

def test_scan_creates_sorted_pending_manifest(tmp_path, capsys):
    covers = make_covers(tmp_path, ["b.jpg", "a.jpg", "c.png", "note.txt"])
    out = tmp_path / "batch_manifest.json"
    bic.main(["scan", "--dir", str(covers), "--out", str(out)])

    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert [e["file"] for e in manifest["entries"]] == ["a.jpg", "b.jpg", "c.png"]
    assert all(e["status"] == "pending" for e in manifest["entries"])
    assert manifest["source_dir"] == str(covers)


def test_scan_merge_preserves_edits_and_adds_new(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    out = tmp_path / "batch_manifest.json"
    bic.main(["scan", "--dir", str(covers), "--out", str(out)])

    # 用户编辑：a 识别并确认；目录变化：新增 b、删除 a 后重扫
    manifest = json.loads(out.read_text(encoding="utf-8"))
    manifest["entries"][0].update(title="三体", author="刘慈欣", status="confirmed")
    out.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (covers / "a.jpg").unlink()
    make_covers(tmp_path, ["b.jpg"])
    bic.main(["scan", "--dir", str(covers), "--out", str(out)])

    manifest = json.loads(out.read_text(encoding="utf-8"))
    files = {e["file"]: e for e in manifest["entries"]}
    assert files["a.jpg"]["status"] == "confirmed" and files["a.jpg"]["title"] == "三体"
    assert files["b.jpg"]["status"] == "pending"
    assert "不在目录: a.jpg" in capsys.readouterr().out


def test_scan_rejects_manifest_of_other_dir(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    out = tmp_path / "batch_manifest.json"
    write_manifest(out, tmp_path / "elsewhere", [])
    with pytest.raises(SystemExit):
        bic.main(["scan", "--dir", str(covers), "--out", str(out)])


def test_status_reports_counts_and_missing_keys(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    out = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="三体"),
        entry(None, "recognized", isbn="9787506365437"),  # 无文件但isbn齐全，合法
        entry(None, "recognized"),  # 既无文件也无识别键
    ])
    bic.main(["status", "--manifest", str(out)])
    stdout = capsys.readouterr().out
    assert "confirmed" in stdout and "2" in stdout
    assert "缺 isbn 和 title" in stdout


# ── run ──

def test_run_dry_run_no_api_no_manifest_write(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="三体", author="刘慈欣"),
        entry("b.jpg", "confirmed"),  # 缺识别键 → 预检失败
    ])
    fake = FakeClient()
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--dry-run"), client=fake)

    assert rc == 0 and fake.calls == []
    out = capsys.readouterr().out
    assert "dry-run" in out and "三体" in out and "预检失败 1" in out
    # 清单不回写：b 仍为 confirmed（失败只在内存中标记）
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"][1]["status"] == "confirmed"
    assert not (tmp_path / "r.json").exists()


def test_run_imports_confirmed_only_and_classifies(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="三体", author="刘慈欣"),
        entry("b.jpg", "confirmed", isbn="9787020002207"),
        entry("c.jpg", "recognized", title="活着"),   # 未经确认，不带 --yes 不入库
        entry("d.jpg", "skip"),
    ])
    fake = FakeClient(behavior={"三体": created_resp(5), "9787020002207": exists_resp(9)})
    report_path = tmp_path / "r.json"
    rc = bic.cmd_run(run_args(manifest, report_path), client=fake)

    assert rc == 0 and len(fake.calls) == 2
    statuses = {e["file"]: e["status"] for e in json.loads(manifest.read_text(encoding="utf-8"))["entries"]}
    assert statuses == {"a.jpg": "imported", "b.jpg": "imported", "c.jpg": "recognized", "d.jpg": "skip"}

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"photos_total": 4, "total": 2, "created": 1, "exists": 1,
                                 "failed": 0, "outcome_unknown": 0, "skipped": 2}
    assert report["run_id"].startswith("run-")  # BI-04：运行标识入报告
    by_file = {e["file"]: e for e in report["entries"]}
    assert by_file["a.jpg"]["outcome"] == "created" and by_file["a.jpg"]["book_id"] == 5
    assert by_file["b.jpg"]["outcome"] == "exists" and by_file["b.jpg"]["book_id"] == 9
    assert by_file["a.jpg"]["run_id"] == report["run_id"]
    # 有文件的条目应携带封面图上传
    assert fake.calls[0]["image"] == covers / "a.jpg"


def test_run_marks_failed_and_nonzero_exit(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="三体"),
    ])
    fake = FakeClient(behavior={"三体": RuntimeError("[HTTP 400] 价格必须大于 0")})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)

    assert rc == 1
    saved = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert saved["status"] == "failed"
    assert "价格必须大于 0" in saved["result"]["error"]
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["summary"]["failed"] == 1


def test_run_missing_image_fails_preflight_without_api(tmp_path, capsys):
    covers = make_covers(tmp_path, [])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("ghost.jpg", "confirmed", title="三体"),
    ])
    fake = FakeClient(behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)

    assert rc == 1 and fake.calls == []
    assert "封面文件缺失" in json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]["result"]["error"]
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["summary"] == {"photos_total": 1, "total": 1, "created": 0, "exists": 0,
                                 "failed": 1, "outcome_unknown": 0, "skipped": 0}
    failed_row = next(e for e in report["entries"] if e["outcome"] == "failed")
    assert failed_row["error_code"] == "file_missing"  # BI-04：结构化错误码


def test_run_health_unreachable_exits_before_any_call(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = FakeClient(health_error="无法连接 API")
    with pytest.raises(SystemExit):
        bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert fake.calls == []


# ── BI-02：条码能力预检（复用 /health，不另造诊断入口）──

class HealthDataClient(FakeClient):
    def __init__(self, health_data: dict, **kw):
        super().__init__(**kw)
        self.health_data = health_data

    def health(self):
        if self.health_error:
            raise RuntimeError(self.health_error)
        return {"status": "ok", "data": self.health_data}


def test_run_warns_when_barcode_dependency_unavailable(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = HealthDataClient({"barcode_scan_available": False}, behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    out = capsys.readouterr().out
    assert rc == 0
    assert "条码解码依赖不可用" in out and "降级入库" in out


def test_run_shows_capability_unknown_when_health_auth_protected(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = HealthDataClient({"auth_protected": True, "database": "unknown"},
                            behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert rc == 0
    assert "条码能力未知" in capsys.readouterr().out


def test_run_silent_when_barcode_available(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = HealthDataClient({"barcode_scan_available": True}, behavior={"三体": created_resp()})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    out = capsys.readouterr().out
    assert "条码" not in out.replace("条码解码依赖不可用", "")


# ── --yes 与 eval 门控 ──

GATE_ARGS = ["--gate-task-type", "cover_title_author", "--gate-prompt-version", "vision-cover-v1",
             "--gate-model-fingerprint", "fp-aaaa1111", "--gate-golden-sha", "gs-bbbb2222"]


def _write_eval_result(eval_dir: Path, accuracy: float, *, verified_groups: int = 20,
                       identity: bool = True, task: str = "cover_title_author",
                       prompt: str = "vision-cover-v1", fingerprint: str = "fp-aaaa1111",
                       golden: str = "gs-bbbb2222",
                       verified_accuracy: float | None = None) -> None:
    """BI-10/BUG-257-259 后 eval 证据须携带身份、完成度与门控口径准确率。"""
    if verified_accuracy is None:
        verified_accuracy = accuracy
    data = {"generated_at": "t",
            "metrics": {"book_level_accuracy": accuracy,
                        "book_level_accuracy_verified": verified_accuracy}}
    if identity:
        data["identity"] = {"task_type": task, "prompt_version": prompt,
                            "model_fingerprint": fingerprint, "golden_sha256": golden}
        data["sample"] = {"verified_groups": verified_groups}
        data["progress"] = {"planned_photos": max(verified_groups, 1),
                            "completed_photos": max(verified_groups, 1),
                            "planned_groups": max(verified_groups, 1),
                            "completed_groups": max(verified_groups, 1),
                            "completed_verified_groups": verified_groups,
                            "complete": True, "smoke": False}
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "results-20260819-090000.json").write_text(json.dumps(data), encoding="utf-8")


def test_run_yes_blocked_without_eval_results(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "recognized", title="三体")])
    empty_eval = tmp_path / "eval"
    empty_eval.mkdir()
    with pytest.raises(SystemExit):
        bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", eval_dir=empty_eval), client=FakeClient())


def test_run_yes_gate_blocked_below_threshold(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "recognized", title="三体")])
    eval_dir = tmp_path / "eval"
    _write_eval_result(eval_dir, 0.6)
    with pytest.raises(SystemExit):
        bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", *GATE_ARGS,
                             eval_dir=eval_dir), client=FakeClient())
    assert "低于" in capsys.readouterr().err


def test_run_yes_dry_run_preview_passes_gate_with_warning(tmp_path, capsys):
    """CHK-056：--yes --dry-run 是纯预览，不拦；打印警告并继续。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "recognized", title="三体")])
    empty_eval = tmp_path / "eval"
    empty_eval.mkdir()
    fake = FakeClient(behavior={"三体": created_resp()})
    rc = bic.cmd_run(
        run_args(manifest, tmp_path / "r.json", "--yes", "--dry-run", eval_dir=empty_eval),
        client=fake,
    )
    out = capsys.readouterr().out
    assert rc == 0 and fake.calls == []
    assert "门控未过" in out and "dry-run" in out
    # 清单未被修改（recognized 保持，等待正式 run 前的核对/门控）
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]["status"] == "recognized"


def test_run_yes_passes_at_threshold(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "recognized", title="三体")])
    eval_dir = tmp_path / "eval"
    _write_eval_result(eval_dir, 0.8)
    fake = FakeClient(behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", *GATE_ARGS,
                              eval_dir=eval_dir), client=fake)
    assert rc == 0 and len(fake.calls) == 1
    assert "门控通过" in capsys.readouterr().out


def test_run_yes_force_overrides_gate(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "recognized", title="三体")])
    eval_dir = tmp_path / "eval"
    _write_eval_result(eval_dir, 0.6)
    fake = FakeClient(behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", "--force",
                              eval_dir=eval_dir), client=fake)
    assert rc == 0 and len(fake.calls) == 1


def test_run_retry_failed_includes_failed_entries(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "failed", title="三体", result={"outcome": "failed", "error": "boom"}),
    ])
    fake = FakeClient(behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--retry-failed"), client=fake)
    assert rc == 0 and len(fake.calls) == 1
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]["status"] == "imported"


def test_run_bulk_defaults_filled_by_effective_value(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="三体", location="书架自带"),
        entry("b.jpg", "confirmed", title="活着"),
    ])
    fake = FakeClient(behavior={"三体": created_resp(), "活着": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json",
                              "--location", "客厅A", "--price", "38"), client=fake)
    assert rc == 0
    by_title = {c["title"]: c for c in fake.calls}
    assert by_title["三体"]["location"] == "书架自带"   # 条目自身值优先
    assert by_title["活着"]["location"] == "客厅A" and by_title["活着"]["price"] == 38.0


# ── BI-03：已确认条目显式 prefer_confirmed ──

def test_run_sends_prefer_confirmed_for_confirmed_entries(tmp_path):
    """confirmed 条目按契约 §5 传 prefer_confirmed + confirmed_fields；recognized（--yes）不传。"""
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="翦商", author="李硕"),
        entry("b.jpg", "recognized", title="活着"),
    ])
    fake = FakeClient(behavior={"翦商": created_resp(1), "活着": created_resp(2)})
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    _write_eval_result(eval_dir, 0.9)
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", *GATE_ARGS,
                              eval_dir=eval_dir), client=fake)

    assert rc == 0 and len(fake.calls) == 2
    by_title = {c["title"]: c for c in fake.calls}
    assert by_title["翦商"]["field_policy"] == "prefer_confirmed"
    assert sorted(by_title["翦商"]["confirmed_fields"]) == ["authors", "title"]
    assert "field_policy" not in by_title["活着"]  # 未经人工核对，保持 default


def test_run_respects_manifest_declared_confirmed_fields(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="翦商", isbn="9787553820217",
              confirmed_fields=["title"]),
    ])
    fake = FakeClient(behavior={"9787553820217": created_resp(3)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert fake.calls[0]["field_policy"] == "prefer_confirmed"
    assert fake.calls[0]["confirmed_fields"] == ["title"]


def test_run_report_marks_historical_failed_as_skipped(tmp_path):
    """BUG-170：未被 --retry-failed 的历史 failed 条目，报告应记 skipped 而非 failed。"""
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", title="三体", result={"outcome": "created", "book_id": 5}),
        entry("b.jpg", "failed", title="旧账", result={"outcome": "failed", "error": "上次网络错误"}),
        entry("c.jpg", "confirmed", title="活着"),
    ])
    fake = FakeClient(behavior={"活着": created_resp(7)})
    report_path = tmp_path / "r.json"
    rc = bic.cmd_run(run_args(manifest, report_path), client=fake)

    assert rc == 0 and len(fake.calls) == 1  # 只提交 confirmed 的 c
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"photos_total": 3, "total": 1, "created": 1, "exists": 0,
                                 "failed": 0, "outcome_unknown": 0, "skipped": 2}
    by_file = {e["file"]: e["outcome"] for e in report["entries"]}
    assert by_file == {"a.jpg": "skipped", "b.jpg": "skipped", "c.jpg": "created"}
    # 清单里 b 保持历史 failed 状态不被本次 run 改写
    saved = {e["file"]: e for e in json.loads(manifest.read_text(encoding="utf-8"))["entries"]}
    assert saved["b.jpg"]["status"] == "failed" and "上次网络错误" in saved["b.jpg"]["result"]["error"]


# ── BI-04：结果未知、逐条落盘、run_id、历史报告 ──

def _timeout_exc():
    from bookshelf.client import ApiTimeoutError
    return ApiTimeoutError("连接 API 超时：http://127.0.0.1:8000（POST /books/intake）——回执未知，核对后再决定是否重试")


def test_run_read_timeout_marks_outcome_unknown_not_failed(tmp_path, capsys):
    """契约 §7：读超时 = 结果未知，不并入 failed，退出码非 0，自动重试暂停。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = FakeClient(behavior={"三体": _timeout_exc()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)

    assert rc == 1
    saved = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert saved["status"] == "outcome_unknown"
    assert saved["result"]["error_code"] == "timeout"
    assert saved["result"]["run_id"].startswith("run-")
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["summary"]["outcome_unknown"] == 1
    assert report["summary"]["failed"] == 0
    assert "结果未知" in capsys.readouterr().out


def test_retry_failed_excludes_outcome_unknown(tmp_path):
    """--retry-failed 不得自动重试结果未知条目（契约 §4.2）。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "outcome_unknown", title="三体",
              result={"outcome": "outcome_unknown", "error_code": "timeout", "error": "回执未知"}),
    ])
    fake = FakeClient(behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--retry-failed"), client=fake)
    assert rc == 0 and fake.calls == []  # 未被重试
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]["status"] == "outcome_unknown"


def test_retry_unknown_explicitly_resends(tmp_path):
    """核对后显式 --retry-unknown 才重发结果未知条目。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "outcome_unknown", title="三体",
              result={"outcome": "outcome_unknown", "error_code": "timeout", "error": "回执未知"}),
    ])
    fake = FakeClient(behavior={"三体": created_resp(11)})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--retry-unknown"), client=fake)
    assert rc == 0 and len(fake.calls) == 1
    saved = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert saved["status"] == "imported" and saved["result"]["book_id"] == 11


def test_interrupt_midway_preserves_progress_and_report(tmp_path):
    """验收锚点：处理第 N 条后中断，前 N-1 条的回执已在磁盘（清单 + 报告快照）。"""
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="三体"),
        entry("b.jpg", "confirmed", title="活着"),
        entry("c.jpg", "confirmed", title="围城"),
    ])

    class InterruptingClient(FakeClient):
        def add(self, **kwargs):
            if kwargs.get("title") == "活着":
                raise KeyboardInterrupt("模拟进程中断")
            return super().add(**kwargs)

    fake = InterruptingClient(behavior={"三体": created_resp(5), "围城": created_resp(6)})
    with pytest.raises(KeyboardInterrupt):
        bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)

    saved = {e["file"]: e for e in json.loads(manifest.read_text(encoding="utf-8"))["entries"]}
    assert saved["a.jpg"]["status"] == "imported"
    assert saved["a.jpg"]["result"]["book_id"] == 5
    assert saved["a.jpg"]["result"]["run_id"].startswith("run-")
    assert saved["b.jpg"]["status"] == "confirmed"  # 未处理条目保持原状，可恢复
    assert saved["c.jpg"]["status"] == "confirmed"
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["summary"]["created"] == 1  # 中断前的快照
    assert not (tmp_path / "r.json.tmp").exists()  # 原子替换不留半写文件


def test_default_report_name_contains_run_timestamp(tmp_path, monkeypatch):
    """默认报告名带 run 时间戳：重跑不覆写唯一历史（契约 §6）。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = FakeClient(behavior={"三体": created_resp()})
    monkeypatch.chdir(tmp_path)
    rc = bic.cmd_run(bic.build_parser().parse_args(
        ["run", "--manifest", str(manifest)]), client=fake)
    assert rc == 0
    reports = list(tmp_path.glob("batch_report-*.json"))
    assert len(reports) == 1 and reports[0].name != "batch_report.json"


def test_response_warnings_flow_into_result(tmp_path):
    """后端警告（条码降级/字段冲突）落到条目 result.warnings 与报告行。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    resp = {"ok": True, "data": {
        "already_exists": False, "book": {"id": 8}, "action": "created_new", "message": "ok",
        "warnings": [{"code": "barcode_not_found", "message": "图片中未发现可解码的 ISBN 条码"}],
    }}
    fake = FakeClient(behavior={"三体": resp})
    report_path = tmp_path / "r.json"
    bic.cmd_run(run_args(manifest, report_path), client=fake)
    saved = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert saved["result"]["warnings"][0]["code"] == "barcode_not_found"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    row = next(e for e in report["entries"] if e["file"] == "a.jpg")
    assert row["warnings"][0]["code"] == "barcode_not_found"


# ── BUG-260：确认保护跨重试（资格独立于运行状态）──

def test_run_retry_failed_keeps_prefer_confirmed(tmp_path):
    """confirmed 条目首次失败改 failed 后，--retry-failed 重试仍传 prefer_confirmed。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "failed", title="翦商", author="李硕",
              confirmed_fields=["title", "authors"],
              result={"outcome": "failed", "error_code": "bad_request", "error": "上次失败"}),
    ])
    fake = FakeClient(behavior={"翦商": created_resp(3)})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--retry-failed"), client=fake)

    assert rc == 0 and len(fake.calls) == 1
    assert fake.calls[0]["field_policy"] == "prefer_confirmed"
    assert fake.calls[0]["confirmed_fields"] == ["title", "authors"]
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]["status"] == "imported"


def test_run_retry_unknown_keeps_prefer_confirmed(tmp_path):
    """confirmed 条目结果未知（outcome_unknown）核对后重试，确认字段仍受保护。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "outcome_unknown", title="翦商", author="李硕",
              confirmed_fields=["title", "authors"],
              result={"outcome": "outcome_unknown", "error_code": "timeout", "error": "回执未知"}),
    ])
    fake = FakeClient(behavior={"翦商": created_resp(4)})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--retry-unknown"), client=fake)

    assert rc == 0 and len(fake.calls) == 1
    assert fake.calls[0]["field_policy"] == "prefer_confirmed"
    assert sorted(fake.calls[0]["confirmed_fields"]) == ["authors", "title"]


def test_run_confirmed_infers_fields_from_fallback_keys(tmp_path):
    """confirmed 条目无显式 confirmed_fields 时按 title/author/isbn 推断（含 authors）。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="翦商", author="李硕", isbn="9787553820217"),
    ])
    fake = FakeClient(behavior={"9787553820217": created_resp(5)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert fake.calls[0]["field_policy"] == "prefer_confirmed"
    assert sorted(fake.calls[0]["confirmed_fields"]) == ["authors", "isbn", "title"]


# ── BUG-262：作者数组从清单传到 CLI/后端 ──

def test_run_passes_authors_array_from_manifest(tmp_path):
    """manifest authors 数组完整传给 client.add，不回落成 author=None。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="雾中灯塔", authors=["陈予安", "林晚"]),
    ])
    fake = FakeClient(behavior={"雾中灯塔": created_resp(6)})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)

    assert rc == 0 and len(fake.calls) == 1
    assert fake.calls[0]["authors"] == ["陈予安", "林晚"]
    # 实际下发的数组落入回执与报告，清单侧可追溯（不静默丢弃）
    saved = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert saved["result"]["authors"] == ["陈予安", "林晚"]
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    row = next(e for e in report["entries"] if e["file"] == "a.jpg")
    assert row["authors"] == ["陈予安", "林晚"]


def test_run_authors_fallback_to_singular_author(tmp_path):
    """只有 singular author 时回落为单元素数组（与后端 payload.author 等价语义）。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="活着", author="余华"),
    ])
    fake = FakeClient(behavior={"活着": created_resp(7)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert fake.calls[0]["authors"] == ["余华"]


def test_run_confirmed_fields_covers_authors_array(tmp_path):
    """确认字段推断覆盖数组：authors 数组 + title → confirmed_fields 含 authors。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", title="雾中灯塔", authors=["陈予安", "林晚"]),
    ])
    fake = FakeClient(behavior={"雾中灯塔": created_resp(8)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert fake.calls[0]["field_policy"] == "prefer_confirmed"
    assert sorted(fake.calls[0]["confirmed_fields"]) == ["authors", "title"]


# ── BUG-264：冲突明细（field/detail）完整保留 ──

def test_run_report_preserves_conflict_field_detail(tmp_path):
    """metadata_field_conflict 的 field/确认值/外部值/来源保留在清单与报告中。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    detail = {"confirmed": ["刘慈欣"], "metadata": ["刘慈欣著"], "source": "douban"}
    resp = {"ok": True, "data": {
        "already_exists": False, "book": {"id": 8}, "action": "created_new", "message": "ok",
        "warnings": [{"code": "metadata_field_conflict",
                      "message": "外部元数据authors与确认值不一致，已保留确认值",
                      "field": "authors", "detail": detail}],
    }}
    fake = FakeClient(behavior={"三体": resp})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)

    saved = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    warning = saved["result"]["warnings"][0]
    assert warning["field"] == "authors"
    assert warning["detail"] == detail  # 确认值/外部值/来源完整
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    row = next(e for e in report["entries"] if e["file"] == "a.jpg")
    assert row["warnings"][0]["detail"]["source"] == "douban"


# ── BUG-261：回执丢失（可能已提交）归类 outcome_unknown ──

def _receipt_lost_exc():
    from bookshelf.client import ApiOutcomeUnknownError
    return ApiOutcomeUnknownError(
        "回执丢失（可能已提交）：http://127.0.0.1:8000（POST /books/intake，ReadError）"
        "——请求可能已被受理，核对后再决定是否重试")


def test_run_receipt_lost_marks_outcome_unknown_not_failed(tmp_path, capsys):
    """连接建立后读写失败 = 可能已提交，必须 outcome_unknown 待核对，不得记 failed。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = FakeClient(behavior={"三体": _receipt_lost_exc()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)

    assert rc == 1
    saved = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert saved["status"] == "outcome_unknown"
    assert saved["result"]["error_code"] == "receipt_lost"
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["summary"]["outcome_unknown"] == 1
    assert report["summary"]["failed"] == 0


def test_retry_failed_excludes_receipt_lost(tmp_path):
    """--retry-failed 不得重发可能已提交的请求（防重复写入，覆盖购买/副本场景）。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "outcome_unknown", title="三体",
              result={"outcome": "outcome_unknown", "error_code": "receipt_lost", "error": "回执丢失"}),
    ])
    fake = FakeClient(behavior={"三体": created_resp()})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--retry-failed"), client=fake)
    assert rc == 0 and fake.calls == []
