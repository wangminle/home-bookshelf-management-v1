"""BI-05 回归：reconcile 只读核对、显式映射（先核对目标 ISBN）、分口径汇总。

验收锚点：本批 3 条悬空 ID（10/11/12 → 18/30/56）可生成有依据的修正；
无可靠目标保持待核对；数据库无写入（只经 client 读接口）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import batch_import_covers as bic  # noqa: E402


# ── 测试工具 ──

def entry(file, status="pending", **kw):
    base = {"photo_id": None, "file": file, "sha256": None, "role": "cover",
            "title": None, "author": None, "isbn": None,
            "price": None, "channel": None, "location": None, "member_id": None,
            "confirmed_fields": [], "status": status, "note": None,
            "warnings": [], "candidate_id": None, "result": None}
    base.update(kw)
    return base


def write_manifest(path: Path, source_dir: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps(
        {"version": 2, "batch_id": "batch-t", "source_dir": str(source_dir),
         "created_at": "t", "updated_at": "t", "entries": entries},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def book_resp(book_id: int, *, isbn13=None, title=None, cover="covers/x.jpg") -> dict:
    return {"ok": True, "data": {"id": book_id, "title": title or f"书{book_id}",
                                 "isbn13": isbn13, "cover_path": cover}}


class ReconcileClient:
    """show(book_id) 按预设返回书目 / 404 / 网络错误；记录所有调用。"""

    def __init__(self, books: dict | None = None):
        self.books = books or {}
        self.calls: list[int] = []

    def show(self, book_id: int) -> dict:
        self.calls.append(book_id)
        behavior = self.books.get(book_id)
        if behavior is None:
            raise RuntimeError(f"[HTTP 404] 书目不存在: {book_id}")
        if isinstance(behavior, Exception):
            raise behavior
        return behavior

    def health(self):
        return {"status": "ok"}


def rec_args(manifest: Path, out: Path, *extra: str):
    argv = ["reconcile", "--manifest", str(manifest), "--out", str(out)]
    return bic.build_parser().parse_args(argv + list(extra))


# ── 只读核对与分口径汇总 ──

def test_reconcile_readonly_reports_calibers(tmp_path, capsys):
    """契约 §6：照片总数/最终关联/净新增分口径；未处理条目不算未解决关联。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        # 2 张照片 → 同一本书 18（重复封面）；1 张命中已有书 9（outcome=exists）
        entry("a.jpg", "imported", isbn="9787553820217",
              result={"outcome": "created", "book_id": 18, "run_id": "run-1"}),
        entry("a2.jpg", "imported",
              result={"outcome": "created", "book_id": 18, "run_id": "run-1"}),
        entry("b.jpg", "imported",
              result={"outcome": "exists", "book_id": 9, "run_id": "run-1"}),
        entry("c.jpg", "skip"),   # 跳过条目计入照片总数
    ])
    client = ReconcileClient({
        18: book_resp(18, isbn13="9787553820217"),
        9: book_resp(9, isbn13="9787020002207"),
    })
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest, out), client=client)

    assert rc == 0  # 无未解决项
    report = json.loads(out.read_text(encoding="utf-8"))
    s = report["summary"]
    assert s["photos_total"] == 4
    assert s["photos_linked"] == 3
    assert s["distinct_book_ids"] == 2
    assert s["net_created"] == 1  # 只有 created 且存活的 18；exists 的 9 不算新增
    assert s["unresolved"] == {"missing_book": 0, "unknown": 0, "isbn_mismatch": 0}
    # 只读：清单未被修改
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["entries"][0]["result"]["book_id"] == 18
    assert "reconciliations" not in saved
    assert "核对" in capsys.readouterr().out


def test_reconcile_flags_missing_and_unknown_books(tmp_path, capsys):
    """悬空 ID（404）与状态未知（网络错误）分别统计，条目保持待核对。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", result={"outcome": "created", "book_id": 10}),
        entry("b.jpg", "imported", result={"outcome": "created", "book_id": 11}),
    ])
    client = ReconcileClient({
        10: book_resp(10),  # 等等——10 应为 404；此处显式不注册即 404
    })
    # 10/11 都未注册 → 404；再让 12 走网络错误
    client.books = {
        12: RuntimeError("无法连接 API：http://127.0.0.1:8000（ConnectError）"),
    }
    manifest2 = write_manifest(tmp_path / "m2.json", covers, [
        entry("a.jpg", "imported", result={"outcome": "created", "book_id": 10}),
        entry("b.jpg", "imported", result={"outcome": "created", "book_id": 12}),
    ])
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest2, out), client=client)
    assert rc == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    s = report["summary"]["unresolved"]
    assert s == {"missing_book": 1, "unknown": 1, "isbn_mismatch": 0}
    rows = {r["file"]: r for r in report["entries"]}
    assert rows["a.jpg"]["book_alive"] is False
    assert rows["b.jpg"]["book_alive"] is None
    assert "未解决" in capsys.readouterr().out


def test_reconcile_flags_isbn_mismatch_between_entry_and_live_book(tmp_path):
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", isbn="9787553820217",
              result={"outcome": "created", "book_id": 18}),
    ])
    client = ReconcileClient({18: book_resp(18, isbn13="9787506365437")})  # ISBN 与条目不符
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest, out), client=client)
    assert rc == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["unresolved"]["isbn_mismatch"] == 1
    rows = {r["file"]: r for r in report["entries"]}
    assert rows["a.jpg"]["isbn_match"] is False


# ── BUG-277 回归：结果未知且无 book_id 的条目必须计入未解决 ──

def test_reconcile_counts_outcome_unknown_without_book_id(tmp_path, capsys):
    """outcome_unknown 回执通常没有 book_id：不得按"全部已关联"静默返回 0，
    须计入未解决（unknown 口径）、给条目打待核对标记、返回非零。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "outcome_unknown", isbn="9787553820217", title="翦商",
              result={"outcome": "outcome_unknown", "book_id": None,
                      "error_code": "read_timeout", "run_id": "run-1"}),
        entry("b.jpg", "imported", isbn="9787020002207",
              result={"outcome": "created", "book_id": 18, "run_id": "run-1"}),
    ])
    client = ReconcileClient({18: book_resp(18, isbn13="9787020002207")})
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest, out), client=client)

    assert rc == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["unresolved"] == {"missing_book": 0, "unknown": 1,
                                               "isbn_mismatch": 0}
    rows = {r["file"]: r for r in report["entries"]}
    assert rows["a.jpg"]["pending_review"]
    assert "retry-unknown" in rows["a.jpg"]["pending_review"]
    # 正常关联的条目不受影响
    assert rows["b.jpg"]["book_alive"] is True
    assert "结果未知" in capsys.readouterr().out


def test_reconcile_outcome_unknown_with_book_id_still_linked(tmp_path):
    """结果未知但回执带 book_id 的条目走正常存活核对，不重复计入未解决。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "outcome_unknown",
              result={"outcome": "outcome_unknown", "book_id": 18, "run_id": "run-1"}),
    ])
    client = ReconcileClient({18: book_resp(18)})
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest, out), client=client)
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["photos_linked"] == 1
    assert report["summary"]["unresolved"] == {"missing_book": 0, "unknown": 0,
                                               "isbn_mismatch": 0}


# ── BUG-278 回归：ISBN-10 ↔ ISBN-13 统一换算后再比较 ──

def test_reconcile_isbn10_entry_matches_isbn13_live_book(tmp_path):
    """清单 ISBN-10（702000220X）与后端 ISBN-13（9787020002207）为同一本书，
    换算后必须判相符，不得误报 ISBN 不符。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", isbn="702000220X",
              result={"outcome": "created", "book_id": 18}),
    ])
    client = ReconcileClient({18: book_resp(18, isbn13="9787020002207")})
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest, out), client=client)

    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["unresolved"]["isbn_mismatch"] == 0
    assert report["entries"][0]["isbn_match"] is True


def test_reconcile_genuine_isbn_mismatch_after_canonicalization(tmp_path):
    """真实的不符（换算后仍不同）仍须报出，不能因为换算而漏报。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", isbn="702000220X",
              result={"outcome": "created", "book_id": 18}),
    ])
    client = ReconcileClient({18: book_resp(18, isbn13="9787506365437")})
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest, out), client=client)

    assert rc == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["unresolved"]["isbn_mismatch"] == 1
    assert report["entries"][0]["isbn_match"] is False


def test_reconcile_map_accepts_isbn10_verification_value(tmp_path):
    """映射核对值给 ISBN-10 时，换算后与目标 ISBN-13 相符即可重定向。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", isbn="702000220X",
              result={"outcome": "created", "book_id": 10, "run_id": "run-old"}),
    ])
    client = ReconcileClient({18: book_resp(18, isbn13="9787020002207")})
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(
        rec_args(manifest, out, "--apply", "--map", "10=18:702000220X"), client=client)

    assert rc == 0
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["entries"][0]["result"]["final_book_id"] == 18
    assert saved["reconciliations"][0]["verified_isbn13"] == "9787020002207"


# ── 显式映射（先核对目标 ISBN）──

def test_reconcile_applies_verified_mapping_with_traceability(tmp_path, capsys):
    """验收锚点：10=18 映射核对 ISBN 通过后应用；原始回执（book_id=10）不可变，
    最终关联单独记 final_book_id，留追溯记录；不改数据库。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", isbn="9787553820217",
              result={"outcome": "created", "book_id": 10, "run_id": "run-old"}),
    ])
    client = ReconcileClient({
        18: book_resp(18, isbn13="9787553820217", title="翦商"),
        # 10 未注册 → 404（悬空）
    })
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(
        rec_args(manifest, out, "--apply", "--map", "10=18:9787553820217"), client=client)

    assert rc == 0
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    e = saved["entries"][0]
    assert e["result"]["book_id"] == 10          # 原始创建回执不可变
    assert e["result"]["final_book_id"] == 18    # 最终关联单独记录
    rec = saved["reconciliations"][0]
    assert rec == {
        "photo_id": e["photo_id"], "file": "a.jpg",
        "from_book_id": 10, "to_book_id": 18,
        "verified_isbn13": "9787553820217",
        "reason": "explicit-map+isbn-verified",
        "at": rec["at"], "operator": "reconcile",
    }
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["applied"] is True
    assert report["summary"]["corrections"]["remapped"] == 1
    assert report["summary"]["unresolved"]["missing_book"] == 0
    row = report["entries"][0]
    assert row["book_id"] == 10 and row["final_book_id"] == 18
    # 重映射目标（既有书 18）不算本批净新增
    assert report["summary"]["net_created"] == 0
    # 最终关联核对 18；净新增仍需独立核对原创建 10（此例已删除）。
    assert client.calls == [18, 10]


def test_reconcile_rejects_mapping_when_target_isbn_mismatches(tmp_path, capsys):
    """映射目标 ISBN 与核对值不符 → 拒绝重定向，条目保持待核对（验收锚点）。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", result={"outcome": "created", "book_id": 11}),
    ])
    client = ReconcileClient({30: book_resp(30, isbn13="9780000000002")})
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(
        rec_args(manifest, out, "--apply", "--map", "11=30:9781111111111"), client=client)

    assert rc == 1
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["entries"][0]["result"]["book_id"] == 11  # 未改
    assert "reconciliations" not in saved
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["corrections"]["remaps_rejected"] == 1
    rows = {r["file"]: r for r in report["entries"]}
    assert "ISBN" in rows["a.jpg"]["pending_review"]
    assert "不符" in capsys.readouterr().out


def test_reconcile_rejects_mapping_when_target_missing(tmp_path):
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", result={"outcome": "created", "book_id": 12}),
    ])
    client = ReconcileClient({})  # 56 未注册 → 404
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(rec_args(manifest, out, "--apply", "--map", "12=56"), client=client)
    assert rc == 1
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["entries"][0]["result"]["book_id"] == 12
    assert "reconciliations" not in saved


def test_reconcile_dry_run_does_not_modify_manifest(tmp_path, capsys):
    """演练（无 --apply）：报告给出建议，清单不动。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", result={"outcome": "created", "book_id": 10}),
    ])
    client = ReconcileClient({18: book_resp(18, isbn13="9787553820217")})
    out = tmp_path / "rec.json"
    bic.cmd_reconcile(rec_args(manifest, out, "--map", "10=18:9787553820217"), client=client)

    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["entries"][0]["result"]["book_id"] == 10
    assert "reconciliations" not in saved
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["applied"] is False
    assert report["summary"]["corrections"]["remapped_proposed"] == 1
    assert "演练" in capsys.readouterr().out


def test_reconcile_mapping_without_affected_entries_is_ignored(tmp_path, capsys):
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", result={"outcome": "created", "book_id": 18}),
    ])
    client = ReconcileClient({30: book_resp(30)})
    out = tmp_path / "rec.json"
    bic.cmd_reconcile(rec_args(manifest, out, "--map", "10=30"), client=client)
    assert "没有条目引用" in capsys.readouterr().out


def test_reconcile_invalid_mapping_spec_exits(tmp_path):
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [])
    with pytest.raises(SystemExit):
        bic.cmd_reconcile(rec_args(manifest, tmp_path / "r.json", "--map", "abc"), client=ReconcileClient())


# ── 口径样例（20260930 批次的缩影）──

def test_reconcile_caliber_sample_71_photos(tmp_path):
    """71 张照片 → 67 本最终书：70 created（3 条后删）→ 67 净新增 + 1 exists。

    用缩样验证：photos_linked 含重定向后的条目；删除且未映射的进未解决。
    """
    covers = tmp_path / "covers"
    covers.mkdir()
    entries = []
    # 4 本存活（id 18/30/56/67），其中 3 本由悬空 ID 10/11/12 重定向而来
    entries.append(entry("p1.jpg", "imported", isbn="9787553820217",
                         result={"outcome": "created", "book_id": 10, "run_id": "r1"}))
    entries.append(entry("p2.jpg", "imported",
                         result={"outcome": "created", "book_id": 11, "run_id": "r1"}))
    entries.append(entry("p3.jpg", "imported",
                         result={"outcome": "created", "book_id": 12, "run_id": "r1"}))
    # 重复封面命中同一本 67
    entries.append(entry("p4.jpg", "imported",
                         result={"outcome": "created", "book_id": 67, "run_id": "r1"}))
    entries.append(entry("p5.jpg", "imported",
                         result={"outcome": "created", "book_id": 67, "run_id": "r1"}))
    # 1 张命中已有书
    entries.append(entry("p6.jpg", "imported",
                         result={"outcome": "exists", "book_id": 9, "run_id": "r1"}))
    manifest = write_manifest(tmp_path / "m.json", covers, entries)
    client = ReconcileClient({
        18: book_resp(18, isbn13="9787553820217"),
        30: book_resp(30, isbn13="9780000000030"),
        56: book_resp(56, isbn13="9780000000056"),
        67: book_resp(67, isbn13="9780000000067"),
        9: book_resp(9, isbn13="9780000000009"),
    })
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(
        rec_args(manifest, out, "--apply",
                 "--map", "10=18:9787553820217", "--map", "11=30:9780000000030",
                 "--map", "12=56:9780000000056"),
        client=client)

    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    s = report["summary"]
    assert s["photos_total"] == 6
    assert s["photos_linked"] == 6
    assert s["distinct_book_ids"] == 5      # 18/30/56/67/9
    # BUG-263：净新增仅"可证明由本批创建且仍存活"——重映射目标（既有书）
    # 不算本批净新增，只有 67
    assert s["net_created"] == 1
    assert s["corrections"]["remapped"] == 3
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert [e["result"]["book_id"] for e in saved["entries"]] == [10, 11, 12, 67, 67, 9]
    assert [e["result"].get("final_book_id") for e in saved["entries"]] == [18, 30, 56, None, None, None]
    assert len(saved["reconciliations"]) == 3


# ── BUG-263 回归：回执不可变 + 净新增口径 + 重放幂等 ──

def test_reconcile_reapply_is_idempotent_and_receipt_stable(tmp_path):
    """重复 --apply 同一映射：回执保持不可变、不重复追加追溯记录。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", isbn="9787553820217",
              result={"outcome": "created", "book_id": 10, "run_id": "run-old"}),
    ])
    client = ReconcileClient({18: book_resp(18, isbn13="9787553820217")})
    argv = rec_args(manifest, tmp_path / "rec.json", "--apply", "--map", "10=18:9787553820217")

    assert bic.cmd_reconcile(argv, client=client) == 0
    assert bic.cmd_reconcile(argv, client=client) == 0  # 二次应用

    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["entries"][0]["result"]["book_id"] == 10
    assert saved["entries"][0]["result"]["final_book_id"] == 18
    assert len(saved["reconciliations"]) == 1  # 不重复记录


def test_reconcile_net_created_counts_only_proven_surviving(tmp_path):
    """净新增口径：本批创建且存活（67）计 1；重映射目标（18）与命中已有书（9）不计。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "imported", result={"outcome": "created", "book_id": 10}),
        entry("b.jpg", "imported", result={"outcome": "created", "book_id": 67}),
        entry("c.jpg", "imported", result={"outcome": "exists", "book_id": 9}),
    ])
    client = ReconcileClient({
        18: book_resp(18, isbn13="9787553820217"),
        67: book_resp(67, isbn13="9780000000067"),
        9: book_resp(9, isbn13="9780000000009"),
    })
    out = tmp_path / "rec.json"
    rc = bic.cmd_reconcile(
        rec_args(manifest, out, "--apply", "--map", "10=18:9787553820217"), client=client)

    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["net_created"] == 1
    assert report["summary"]["photos_linked"] == 3
    assert report["summary"]["distinct_book_ids"] == 3  # 18/67/9
