"""CHK-148：未提交变更的边界回归。"""
import json
from unittest.mock import MagicMock

import pytest

from test_opt016_pairing_verify import pair, reload_manifest, by_photo, verify_args
from test_batch_import_covers import run_args, created_resp, entry, write_manifest, make_covers
import batch_import_covers as bic
import batch_manifest as bm
from bookshelf.credentials import TokenFileError


def test_later_confirmed_barcode_links_existing_book_and_verify_suggests_isbn(tmp_path):
    """封面已单独入库之后，未写入结果的条码仍要能被 verify 指回那本书。"""
    _, path, _ = pair(tmp_path, root_status="imported", member_status="recognized", root_kw={
        "title": "三体", "author": "刘慈欣", "result": {"outcome": "created", "book_id": 5}})
    reader = MagicMock()
    reader.show.return_value = {"data": {"id": 5, "title": "三体", "authors": ["刘慈欣"], "isbn13": None}}
    out = tmp_path / "v.json"
    assert bic.cmd_verify(verify_args(path, out), client=reader) == 1
    suggestions = [r.get("suggestion", "") for r in reload_manifest(out)["entries"]]
    assert any("set-isbn --id 5" in text and "9787536692930" in text for text in suggestions)


def _passing_eval(tmp_path):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "results-ok.json").write_text(json.dumps({
        "generated_at": "2026-10-19T00:00:00",
        "metrics": {"book_level_accuracy_verified": 0.9},
        "identity": {
            "task_type": "cover_title_author",
            "prompt_version": "p1",
            "model_fingerprint": "m1",
            "golden_sha256": "abc",
        },
        "progress": {
            "planned_photos": 20,
            "completed_photos": 20,
            "completed_verified_groups": 20,
        },
    }), encoding="utf-8")
    return eval_dir


def test_yes_rejects_conflicting_isbn_from_recognized_member(tmp_path, capsys):
    """--yes 且门控通过时，confirmed 主条目与 recognized 成员的不同 ISBN 必须拒绝。"""
    eval_dir = _passing_eval(tmp_path)
    _, path, fake = pair(
        tmp_path, root_status="confirmed", member_status="recognized",
        root_kw={"title": "三体", "author": "刘慈欣", "isbn": "9787020002207"})
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        bic.cmd_run(run_args(
            path, tmp_path / "r.json", "--yes",
            "--gate-task-type", "cover_title_author",
            "--gate-prompt-version", "p1",
            "--gate-model-fingerprint", "m1",
            "--gate-golden-sha", "abc",
            eval_dir=eval_dir,
        ), client=fake)
    assert "ISBN 冲突" in capsys.readouterr().err
    assert fake.calls == [] and path.read_bytes() == before


def test_group_conflicting_isbns_aborts_without_mutation_or_api(tmp_path):
    _, path, fake = pair(tmp_path, root_kw={"isbn": "9787020002207", "title": "三体"})
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        bic.cmd_run(run_args(path, tmp_path / "r.json"), client=fake)
    assert fake.calls == [] and path.read_bytes() == before


def test_duplicate_photo_ids_rejected(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg", "b.jpg"])
    path = write_manifest(tmp_path / "m.json", covers, [
        entry("a.jpg", "confirmed", photo_id="p0001", title="a"),
        entry("b.jpg", "confirmed", photo_id="p0001", title="b")])
    with pytest.raises(SystemExit):
        bm.load_manifest(path)


def test_verify_credential_error_aborts_without_report(tmp_path, monkeypatch):
    _, path, _ = pair(tmp_path, root_status="imported", root_kw={"title": "三体", "result": {"book_id": 5}})
    reader = MagicMock()
    reader.show.side_effect = TokenFileError("Token 权限错误")
    out = tmp_path / "v.json"
    with pytest.raises(SystemExit):
        bic.cmd_verify(verify_args(path, out), client=reader)
    assert not out.exists()


def test_late_confirmation_uses_final_mapping_and_dry_run_is_read_only(tmp_path):
    _, path, fake = pair(tmp_path, root_status="imported", root_kw={
        "title": "三体", "result": {"book_id": 5, "final_book_id": 9, "outcome": "created"}})
    before = path.read_bytes()
    bic.cmd_run(run_args(path, tmp_path / "dry.json", "--dry-run"), client=fake)
    assert path.read_bytes() == before and not (tmp_path / "dry.json").exists()
    bic.cmd_run(run_args(path, tmp_path / "run.json"), client=fake)
    assert by_photo(reload_manifest(path))["p0002"]["result"]["book_id"] == 9
    assert fake.calls == []


def test_generated_photo_id_does_not_collide_with_later_explicit_id():
    m = bm.upgrade_manifest({"entries": [{"file": "a.jpg"}, {"file": "b.jpg", "photo_id": "p0001"}]})
    assert len({e["photo_id"] for e in m["entries"]}) == 2


def test_retry_failed_keeps_recognized_partner(tmp_path):
    """--yes 失败后，不带 --yes 的 --retry-failed 仍要把同组 recognized 成员的 ISBN 并进来。"""
    _, path, fake = pair(
        tmp_path, root_status="recognized", member_status="recognized",
        behavior={"9787536692930": RuntimeError("[HTTP 500] 暂时失败")})
    assert bic.cmd_run(run_args(path, tmp_path / "r1.json", "--yes", "--force"), client=fake) == 1
    assert fake.calls and fake.calls[0]["isbn"] == "9787536692930"
    m = by_photo(reload_manifest(path))
    assert m["p0001"]["status"] == "failed" and m["p0002"]["status"] == "recognized"
    retry = type(fake)(behavior={"9787536692930": created_resp(5)})
    assert bic.cmd_run(run_args(path, tmp_path / "r2.json", "--retry-failed"), client=retry) == 0
    assert len(retry.calls) == 1
    assert retry.calls[0]["isbn"] == "9787536692930" and retry.calls[0]["title"] == "三体"


def test_canonical_isbn_unicode_digits_do_not_crash():
    for raw in ("978753669293²", "70200022²X", "９７８７５３６６９２９３０"):
        got = bic._canonical_isbn13(raw)
        assert got is None or (got.isascii() and all("0" <= ch <= "9" or ch == "X" for ch in got))


def test_verify_keeps_both_isbn_and_merge_suggestions(tmp_path):
    _, path, _ = pair(tmp_path, root_status="imported", member_status="imported", root_kw={
        "title": "三体", "result": {"book_id": 1, "outcome": "created"}}, member_kw={
        "isbn": "9787536692930", "result": {"book_id": 2, "outcome": "created"}})
    reader = MagicMock()
    reader.show.side_effect = lambda bid: {"data": {
        "id": bid, "title": "三体", "authors": ["刘慈欣"], "isbn13": None}}
    out = tmp_path / "v.json"
    assert bic.cmd_verify(verify_args(path, out), client=reader) == 1
    member = next(r for r in reload_manifest(out)["entries"] if r["photo_id"] == "p0002")
    assert "set-isbn" in member["suggestion"] and "merge" in member["suggestion"]


def test_batch_script_and_doc_use_lf_newlines():
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    for rel in ("scripts/batch_import_covers.py", "docs/batch-import.md"):
        raw = (root / rel).read_bytes()
        assert b"\r\n" not in raw, rel
        if rel.endswith(".py"):
            assert raw.startswith(b"#!/usr/bin/env python3\n")


def test_equivalent_group_isbns_are_accepted(tmp_path):
    _, path, fake = pair(tmp_path, root_kw={"isbn": "702000220X", "title": "书"},
                         member_kw={"isbn": "9787020002207"}, behavior={"702000220X": created_resp(8)})
    assert bic.cmd_run(run_args(path, tmp_path / "r.json"), client=fake) == 0
    assert len(fake.calls) == 1


def test_reconcile_credential_error_aborts_without_report(tmp_path):
    """reconcile 遇到 TokenFileError 必须立即中止，不得将其吞入 book_unknown 并写报告。"""
    _, path, _ = pair(tmp_path, root_status="imported", root_kw={"title": "三体", "result": {"book_id": 5}})
    reader = MagicMock()
    reader.show.side_effect = TokenFileError("Token 权限错误")
    out = tmp_path / "reconcile.json"
    args = bic.build_parser().parse_args(["reconcile", "--manifest", str(path), "--out", str(out)])
    with pytest.raises(SystemExit):
        bic.cmd_reconcile(args, client=reader)
    assert not out.exists()


def test_late_recognized_partner_links_under_yes_mode(tmp_path):
    """主条目已入库后，同组无复核原因的 recognized 搭档在 --yes 下也应补关联到已有 book_id。"""
    _, path, fake = pair(tmp_path, root_status="imported", member_status="recognized", root_kw={
        "title": "三体", "result": {"book_id": 5, "final_book_id": 12, "outcome": "created"}})
    assert bic.cmd_run(run_args(path, tmp_path / "r.json", "--yes", "--force"), client=fake) == 0
    m = by_photo(reload_manifest(path))
    assert m["p0002"]["status"] == "imported"
    assert m["p0002"]["result"]["outcome"] == "grouped"
    assert m["p0002"]["result"]["book_id"] == 12
    assert fake.calls == []


