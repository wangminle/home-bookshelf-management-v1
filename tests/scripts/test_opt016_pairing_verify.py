"""OPT-016：跨照片配对（same_as）、confidence / needs_review、verify 子命令。

根因（TST-017）：条码图只有 ISBN、封面图只有书名，两条记录没有公共键，被建成两本书。
修复口径：脚本侧先把同一本书的多张照片配成组，一组只发一次入库请求；
低置信结果标记 needs_review 并阻止 --yes 自动入库；verify 只读对比清单与库内记录。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "scripts"))

import batch_import_covers as bic  # noqa: E402
import batch_manifest as bm  # noqa: E402
from test_batch_import_covers import (  # noqa: E402
    FakeClient, created_resp, entry, exists_resp, make_covers, run_args, write_manifest,
)


def reload_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def by_photo(manifest: dict) -> dict:
    return {e["photo_id"]: e for e in manifest["entries"]}


def pair(tmp_path, root_kw=None, member_kw=None, *, root_status="confirmed", member_status="confirmed",
         root_role="cover", member_role="barcode", behavior=None, extra_entries=()):
    covers = make_covers(tmp_path, ["cover.jpg", "barcode.jpg"])
    root = entry("cover.jpg", root_status, photo_id="p0001", role=root_role, **(root_kw or {"title": "三体", "author": "刘慈欣"}))
    member = entry("barcode.jpg", member_status, photo_id="p0002", role=member_role, same_as="p0001",
                   **(member_kw or {"isbn": "9787536692930"}))
    manifest = write_manifest(tmp_path / "m.json", covers, [root, member, *extra_entries])
    fake = FakeClient(behavior=behavior if behavior is not None else {"9787536692930": created_resp(5)})
    return covers, manifest, fake


# ── 清单字段 ──

def test_upgrade_adds_pairing_review_defaults():
    up = bm.upgrade_manifest({"version": 2, "entries": [{"file": "a.jpg", "status": "pending", "photo_id": "p0001"}]})
    e = up["entries"][0]
    assert e["same_as"] is None and e["confidence"] is None and e["needs_review"] is False


def test_upgrade_preserves_explicit_values():
    up = bm.upgrade_manifest({"version": 2, "entries": [
        {"file": "a.jpg", "status": "pending", "photo_id": "p0001", "same_as": "p0009",
         "confidence": 0.42, "needs_review": True}]})
    e = up["entries"][0]
    assert (e["same_as"], e["confidence"], e["needs_review"]) == ("p0009", 0.42, True)


def test_scan_skeleton_contains_new_fields(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    out = tmp_path / "m.json"
    bic.cmd_scan(bic.build_parser().parse_args(["scan", "--dir", str(covers), "--out", str(out)]))
    e = reload_manifest(out)["entries"][0]
    assert e["same_as"] is None and e["confidence"] is None and e["needs_review"] is False


@pytest.mark.parametrize("bad, needle", [
    ({"same_as": "p9999"}, "same_as"),
    ({"same_as": "p0001"}, "自己"),
    ({"confidence": 1.5}, "confidence"),
    ({"confidence": -0.1}, "confidence"),
    ({"confidence": "high"}, "confidence"),
    ({"confidence": True}, "confidence"),
])
def test_load_rejects_invalid_pairing_or_confidence(tmp_path, capsys, bad, needle):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", photo_id="p0001", title="x", **bad)])
    with pytest.raises(SystemExit):
        bm.load_manifest(manifest)
    assert needle in capsys.readouterr().err
    assert reload_manifest(manifest)["entries"][0]["file"] == "a.jpg"  # 未被改写


def test_load_rejects_same_as_cycle(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", photo_id="p0001", title="x", same_as="p0002"),
        entry("b.jpg", "confirmed", photo_id="p0002", title="y", same_as="p0001")])
    with pytest.raises(SystemExit):
        bm.load_manifest(manifest)
    assert "循环" in capsys.readouterr().err


# ── run：分组只发一次请求 ──

def test_group_submits_once_with_merged_fields(tmp_path):
    covers, manifest, fake = pair(tmp_path)
    report = tmp_path / "r.json"
    rc = bic.cmd_run(run_args(manifest, report), client=fake)
    assert rc == 0 and len(fake.calls) == 1
    call = fake.calls[0]
    assert call["isbn"] == "9787536692930" and call["title"] == "三体" and call["author"] == "刘慈欣"
    assert call["image"] == covers / "cover.jpg"
    assert sorted(call["confirmed_fields"]) == ["authors", "isbn", "title"]

    m = by_photo(reload_manifest(manifest))
    assert m["p0001"]["status"] == "imported" and m["p0001"]["isbn"] == "9787536692930"  # 合并值写回主条目
    assert m["p0002"]["status"] == "imported"
    assert m["p0002"]["result"]["outcome"] == "grouped"
    assert m["p0002"]["result"]["book_id"] == 5 and m["p0002"]["result"]["grouped_with"] == "p0001"

    rep = reload_manifest(report)
    outcomes = {e["file"]: e["outcome"] for e in rep["entries"]}
    assert outcomes == {"cover.jpg": "created", "barcode.jpg": "grouped"}
    s = rep["summary"]
    assert s["grouped"] == 1 and s["created"] == 1 and s["skipped"] == 0
    assert s["total"] == s["created"] + s["exists"] + s["failed"] + s["outcome_unknown"] + s["skipped"] + s["grouped"]


def test_without_same_as_two_requests_are_still_sent(tmp_path):
    """对照：未配对时行为不变（正是 TST-017 的重复来源）。"""
    covers = make_covers(tmp_path, ["cover.jpg", "barcode.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("cover.jpg", "confirmed", title="三体", author="刘慈欣"),
        entry("barcode.jpg", "confirmed", isbn="9787536692930")])
    fake = FakeClient(behavior={"三体": created_resp(1), "9787536692930": created_resp(2)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert len(fake.calls) == 2
    assert "grouped" not in reload_manifest(tmp_path / "r.json")["summary"]  # 无分组时汇总键不变


def test_group_exists_outcome_also_groups_member(tmp_path):
    _, manifest, fake = pair(tmp_path, behavior={"9787536692930": exists_resp(9)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    m = by_photo(reload_manifest(manifest))
    assert m["p0002"]["result"] == {**m["p0002"]["result"], "outcome": "grouped", "book_id": 9}


def test_group_prefers_cover_photo_as_image_when_root_is_barcode(tmp_path):
    covers = make_covers(tmp_path, ["cover.jpg", "barcode.jpg"])
    root = entry("barcode.jpg", "confirmed", photo_id="p0001", role="barcode", isbn="9787536692930")
    member = entry("cover.jpg", "confirmed", photo_id="p0002", role="cover", same_as="p0001", title="三体", author="刘慈欣")
    manifest = write_manifest(tmp_path / "m.json", covers, [root, member])
    fake = FakeClient(behavior={"9787536692930": created_resp(5)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert len(fake.calls) == 1
    assert fake.calls[0]["image"] == covers / "cover.jpg"
    assert fake.calls[0]["title"] == "三体"


def test_chain_same_as_resolves_to_root(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", photo_id="p0001", role="cover", title="三体"),
        entry("b.jpg", "confirmed", photo_id="p0002", role="barcode", same_as="p0001", isbn="9787536692930"),
        entry("c.jpg", "confirmed", photo_id="p0003", role="other", same_as="p0002")])
    fake = FakeClient(behavior={"9787536692930": created_resp(7)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert len(fake.calls) == 1
    m = by_photo(reload_manifest(manifest))
    assert m["p0002"]["result"]["grouped_with"] == "p0001" and m["p0003"]["result"]["grouped_with"] == "p0001"


def test_unconfirmed_member_holds_the_whole_group(tmp_path):
    """未确认的条码不能被已确认封面丢下：整组留下，避免缺 ISBN 的残缺入库。"""
    _, manifest, fake = pair(tmp_path, member_status="recognized")
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert rc == 0 and fake.calls == []
    m = by_photo(reload_manifest(manifest))
    assert m["p0001"]["status"] == "confirmed" and m["p0002"]["status"] == "recognized"
    assert not m["p0001"].get("result") and not m["p0002"].get("result")


def test_member_held_when_root_not_in_this_run(tmp_path, capsys):
    """主条目未进入本次入库时，成员也不能单独入库——否则又拆成两本。"""
    _, manifest, fake = pair(tmp_path, root_status="recognized", member_status="confirmed")
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert fake.calls == []
    assert "暂缓" in capsys.readouterr().out
    assert by_photo(reload_manifest(manifest))["p0002"]["status"] == "confirmed"
    assert rc == 0


def test_root_failure_leaves_members_untouched(tmp_path):
    err = RuntimeError("[HTTP 400] 参数错误")
    _, manifest, fake = pair(tmp_path, behavior={"9787536692930": err})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert rc == 1
    m = by_photo(reload_manifest(manifest))
    assert m["p0001"]["status"] == "failed"
    assert m["p0002"]["status"] == "confirmed" and not m["p0002"].get("result")
    s = reload_manifest(tmp_path / "r.json")["summary"]
    assert s["failed"] == 1 and s["skipped"] == 1 and "grouped" not in s


def test_dry_run_shows_group_without_calls_or_writes(tmp_path, capsys):
    _, manifest, fake = pair(tmp_path)
    before = manifest.read_bytes()
    bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--dry-run"), client=fake)
    out = capsys.readouterr().out
    assert "p0002" in out and "同书" in out
    assert fake.calls == [] and manifest.read_bytes() == before


# ── needs_review / confidence 与 --yes ──

def _yes_run(tmp_path, entries, *extra):
    covers = make_covers(tmp_path, [e["file"] for e in entries])
    manifest = write_manifest(tmp_path / "m.json", covers, entries)
    fake = FakeClient(behavior={e["title"]: created_resp(i + 1) for i, e in enumerate(entries)})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json", "--yes", "--force", *extra), client=fake)
    return rc, fake, by_photo(reload_manifest(manifest))


def test_yes_holds_needs_review_entries(tmp_path, capsys):
    rc, fake, m = _yes_run(tmp_path, [
        entry("a.jpg", "recognized", photo_id="p0001", title="甲", needs_review=True, confidence=0.4),
        entry("b.jpg", "recognized", photo_id="p0002", title="乙", confidence=0.95)])
    assert [c["title"] for c in fake.calls] == ["乙"]
    assert m["p0001"]["status"] == "recognized"  # 仍待人工核对
    out = capsys.readouterr().out
    assert "needs_review" in out and "甲" in out
    assert rc == 0


def test_yes_min_confidence_holds_low_and_missing(tmp_path):
    _, fake, m = _yes_run(tmp_path, [
        entry("a.jpg", "recognized", photo_id="p0001", title="高", confidence=0.9),
        entry("b.jpg", "recognized", photo_id="p0002", title="低", confidence=0.5),
        entry("c.jpg", "recognized", photo_id="p0003", title="无")], "--min-confidence", "0.8")
    assert [c["title"] for c in fake.calls] == ["高"]
    assert m["p0002"]["status"] == "recognized" and m["p0003"]["status"] == "recognized"


def test_confirmed_entry_with_needs_review_still_runs(tmp_path):
    """人工已确认（status=confirmed）即视为核对完成，needs_review 不再阻止。"""
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", photo_id="p0001", title="甲", needs_review=True)])
    fake = FakeClient(behavior={"甲": created_resp(1)})
    bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert len(fake.calls) == 1


def test_status_reports_groups_and_review_queue(tmp_path, capsys):
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "recognized", photo_id="p0001", title="甲", needs_review=True),
        entry("b.jpg", "recognized", photo_id="p0002", isbn="9787536692930", same_as="p0001"),
        entry("c.jpg", "recognized", photo_id="p0003", title="丙", confidence=0.3)])
    bic.cmd_status(bic.build_parser().parse_args(["status", "--manifest", str(manifest), "--min-confidence", "0.8"]))
    out = capsys.readouterr().out
    assert "分组" in out and "1 组" in out
    assert "待人工核对" in out and "p0001" in out and "p0003" in out


# ── verify ──

class VerifyClient:
    """只提供 show（只读）；任何写方法都会 AttributeError，保证 verify 不写库。"""

    def __init__(self, books: dict):
        self.books = books
        self.shown: list[int] = []

    def show(self, book_id):
        self.shown.append(book_id)
        b = self.books[book_id]
        if isinstance(b, Exception):
            raise b
        return {"ok": True, "data": b}


def imported(file, pid, book_id, **kw):
    return entry(file, "imported", photo_id=pid, result={"outcome": "created", "book_id": book_id}, **kw)


def verify_args(manifest, out, *extra):
    return bic.build_parser().parse_args(["verify", "--manifest", str(manifest), "--out", str(out), *extra])


def run_verify(tmp_path, entries, books, *extra):
    covers = make_covers(tmp_path, [e["file"] for e in entries])
    manifest = write_manifest(tmp_path / "m.json", covers, entries)
    before = manifest.read_bytes()
    client = VerifyClient(books)
    rc = bic.cmd_verify(verify_args(manifest, tmp_path / "v.json", *extra), client=client)
    assert manifest.read_bytes() == before  # 只读
    return rc, json.loads((tmp_path / "v.json").read_text(encoding="utf-8")), client


BOOK_OK = {"title": "三体", "authors": ["刘慈欣"], "isbn13": "9787536692930", "cover_path": "c.jpg"}


def test_verify_all_consistent(tmp_path):
    rc, rep, _ = run_verify(tmp_path, [
        imported("a.jpg", "p0001", 1, title="三体", author="刘慈欣", isbn="9787536692930")], {1: BOOK_OK})
    assert rc == 0
    assert rep["summary"]["checked"] == 1 and rep["summary"]["ok"] == 1 and rep["summary"]["issues"] == {}


def test_verify_flags_isbn_missing_in_db_with_fix_hint(tmp_path):
    rc, rep, _ = run_verify(tmp_path, [
        imported("a.jpg", "p0001", 1, title="三体", isbn="9787536692930")],
        {1: {**BOOK_OK, "isbn13": None}})
    assert rc == 1
    row = rep["entries"][0]
    assert "isbn_missing_in_db" in row["issues"]
    assert "set-isbn" in row["suggestion"] and "--id 1" in row["suggestion"]


def test_verify_flags_isbn_title_author_mismatch(tmp_path):
    rc, rep, _ = run_verify(tmp_path, [
        imported("a.jpg", "p0001", 1, title="活着", author="余华", isbn="9787020002207")],
        {1: BOOK_OK})
    assert rc == 1
    assert {"isbn_mismatch", "title_mismatch", "author_mismatch"} <= set(rep["entries"][0]["issues"])


def test_verify_accepts_isbn10_vs_isbn13_and_subtitle(tmp_path):
    rc, rep, _ = run_verify(tmp_path, [
        imported("a.jpg", "p0001", 1, title="三体", author="刘慈欣", isbn="702000220X")],
        {1: {"title": "三体（全集）", "authors": ["刘慈欣 著"], "isbn13": "9787020002207"}})
    assert rc == 0, rep


def test_verify_detects_possible_duplicate_books(tmp_path):
    rc, rep, _ = run_verify(tmp_path, [
        imported("a.jpg", "p0001", 1, title="影响世界的专利"),
        imported("b.jpg", "p0002", 2, isbn="9787536692930")],
        {1: {"title": "影响世界的专利", "authors": [], "isbn13": None},
         2: {"title": "影响世界的 专利", "authors": [], "isbn13": "9787536692930"}})
    assert rc == 1
    dup = rep["duplicates"]
    assert len(dup) == 1 and sorted(dup[0]["book_ids"]) == [1, 2] and dup[0]["reason"] == "same_title"
    assert "merge" in dup[0]["suggestion"]
    assert rep["summary"]["duplicates"] == 1


def test_verify_detects_group_split(tmp_path):
    rc, rep, _ = run_verify(tmp_path, [
        imported("a.jpg", "p0001", 1, title="三体"),
        imported("b.jpg", "p0002", 2, isbn="9787536692930", same_as="p0001")],
        {1: BOOK_OK, 2: {"title": "x", "authors": [], "isbn13": "9787536692930"}})
    assert rc == 1
    assert "group_split" in {e["photo_id"]: e for e in rep["entries"]}["p0002"]["issues"]


def test_verify_missing_and_unknown_books(tmp_path):
    rc, rep, _ = run_verify(tmp_path, [
        imported("a.jpg", "p0001", 1, title="甲"), imported("b.jpg", "p0002", 2, title="乙")],
        {1: RuntimeError("[HTTP 404] 书籍不存在"), 2: RuntimeError("无法连接 API")})
    by = {e["photo_id"]: e for e in rep["entries"]}
    assert "book_missing" in by["p0001"]["issues"] and "book_unknown" in by["p0002"]["issues"]
    assert rc == 1


def test_verify_follows_final_book_id(tmp_path):
    e = imported("a.jpg", "p0001", 1, title="三体")
    e["result"]["final_book_id"] = 8
    rc, rep, client = run_verify(tmp_path, [e], {8: BOOK_OK})
    assert rc == 0 and client.shown == [8]


def test_verify_lists_review_queue(tmp_path):
    entries = [
        imported("a.jpg", "p0001", 1, title="三体", author="刘慈欣", isbn="9787536692930", needs_review=True),
        imported("b.jpg", "p0002", 1, title="三体", author="刘慈欣", confidence=0.4),
        entry("c.jpg", "recognized", photo_id="p0003", title="未入库", needs_review=True)]
    rc, rep, _ = run_verify(tmp_path, entries, {1: BOOK_OK}, "--min-confidence", "0.8")
    queue = {r["photo_id"]: r["reasons"] for r in rep["review_queue"]}
    assert set(queue) == {"p0001", "p0002", "p0003"}
    assert "needs_review" in queue["p0001"] and "low_confidence" in queue["p0002"]
    assert rep["summary"]["review_queue"] == 3
    assert rc == 0  # 抽查队列是提示，不单独使 verify 失败


def test_verify_skips_entries_without_book_id(tmp_path):
    rc, rep, client = run_verify(tmp_path, [entry("a.jpg", "confirmed", photo_id="p0001", title="甲")], {})
    assert rc == 0 and client.shown == [] and rep["summary"]["checked"] == 0
