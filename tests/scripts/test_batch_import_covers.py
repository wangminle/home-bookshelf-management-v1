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
    assert report["summary"] == {"total": 2, "created": 1, "exists": 1, "failed": 0, "skipped": 2}
    by_file = {e["file"]: e for e in report["entries"]}
    assert by_file["a.jpg"]["outcome"] == "created" and by_file["a.jpg"]["book_id"] == 5
    assert by_file["b.jpg"]["outcome"] == "exists" and by_file["b.jpg"]["book_id"] == 9
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
    assert report["summary"] == {"total": 1, "created": 0, "exists": 0, "failed": 1, "skipped": 0}


def test_run_health_unreachable_exits_before_any_call(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体")])
    fake = FakeClient(health_error="无法连接 API")
    with pytest.raises(SystemExit):
        bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert fake.calls == []


# ── --yes 与 eval 门控 ──

def _write_eval_result(eval_dir: Path, accuracy: float) -> None:
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "results-20260819-090000.json").write_text(json.dumps(
        {"generated_at": "t", "metrics": {"book_level_accuracy": accuracy}}), encoding="utf-8")


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
        bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", eval_dir=eval_dir), client=FakeClient())
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
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", eval_dir=eval_dir), client=fake)
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
    assert report["summary"] == {"total": 1, "created": 1, "exists": 0, "failed": 0, "skipped": 2}
    by_file = {e["file"]: e["outcome"] for e in report["entries"]}
    assert by_file == {"a.jpg": "skipped", "b.jpg": "skipped", "c.jpg": "created"}
    # 清单里 b 保持历史 failed 状态不被本次 run 改写
    saved = {e["file"]: e for e in json.loads(manifest.read_text(encoding="utf-8"))["entries"]}
    assert saved["b.jpg"]["status"] == "failed" and "上次网络错误" in saved["b.jpg"]["result"]["error"]
