"""封面/多模态识书评测：打分规则、合成测试集生成、compare 门控。"""

from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "eval_cover_recognition.py"


def load_eval_mod():
    spec = importlib.util.spec_from_file_location("eval_cover_recognition", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ev():
    return load_eval_mod()


def test_shipped_dataset_covers_all_difficulty_bins(ev):
    golden_path = ROOT / "tests" / "eval" / "golden.json"
    covers = ROOT / "tests" / "eval" / "covers"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert len(golden) >= 12
    seen = {item["difficulty"] for item in golden}
    for diff in ev.DIFFICULTIES:
        assert diff in seen
    for item in golden:
        assert (covers / item["file"]).is_file()


def test_title_match_normalizes_punctuation(ev):
    assert ev.title_matches("三体：死神永生", "三体:死神永生")
    assert ev.title_matches("Sapiens", "sapiens")
    assert not ev.title_matches("三体", "三体Ⅱ")


def test_author_match_any_token(ev):
    assert ev.author_matches("刘慈欣、郝景芳", "刘慈欣")
    assert ev.author_matches("Yuval Noah Harari", "Harari")
    assert not ev.author_matches("刘慈欣", "王小波")


def test_isbn10_to_13_match(ev):
    assert ev.isbn_matches("7536692933", "9787536692930")
    assert not ev.isbn_matches("9787536692930", "9787229030933")


def test_generate_writes_covers_and_golden(ev, tmp_path: Path):
    args = Namespace(eval_dir=str(tmp_path), force=True)
    ev.cmd_generate(args)

    golden_path = tmp_path / "golden.json"
    covers = tmp_path / "covers"
    assert golden_path.is_file()
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert isinstance(golden, list)
    assert len(golden) >= 12
    seen = {item["difficulty"] for item in golden}
    for diff in ev.DIFFICULTIES:
        assert diff in seen
    for item in golden:
        path = covers / item["file"]
        assert path.is_file(), item["file"]
        assert path.stat().st_size > 200
        assert item["expected"].get("title")
        assert item["id"].startswith("vlm-book-")
        assert item["task"] == "cover_title_author"


def test_generate_refuses_overwrite_without_force(ev, tmp_path: Path):
    (tmp_path / "golden.json").write_text("[]\n", encoding="utf-8")
    args = Namespace(eval_dir=str(tmp_path), force=False)
    with pytest.raises(SystemExit):
        ev.cmd_generate(args)


def _write_predictions(path: Path, golden: list, fill) -> None:
    entries = []
    for item in golden:
        pred = fill(item)
        entries.append({"file": item["file"], "predicted": pred, "note": None})
    path.write_text(
        json.dumps({"model": "unit-test", "generated_at": "2026-08-19T00:00:00+08:00", "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_compare_perfect_predictions_pass_gate(ev, tmp_path: Path, capsys):
    ev.cmd_generate(Namespace(eval_dir=str(tmp_path), force=True))
    golden = json.loads((tmp_path / "golden.json").read_text(encoding="utf-8"))
    pred_path = tmp_path / "predictions.json"
    _write_predictions(pred_path, golden, lambda item: dict(item["expected"]))

    code = ev.cmd_compare(
        Namespace(
            golden=str(tmp_path / "golden.json"),
            predictions=str(pred_path),
            eval_dir=str(tmp_path),
            out=str(tmp_path / "results.json"),
            no_write=False,
        )
    )
    assert code == 0
    result = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert result["auto_import_allowed"] is True
    assert result["metrics"]["title_accuracy"] == 1.0
    assert result["metrics"]["author_accuracy"] == 1.0
    assert result["metrics"]["book_level_accuracy"] == 1.0
    captured = capsys.readouterr().out
    assert "允许" in captured


def test_compare_wrong_titles_fail_gate(ev, tmp_path: Path):
    ev.cmd_generate(Namespace(eval_dir=str(tmp_path), force=True))
    golden = json.loads((tmp_path / "golden.json").read_text(encoding="utf-8"))
    pred_path = tmp_path / "predictions.json"
    _write_predictions(
        pred_path,
        golden,
        lambda item: {"title": "错书名", "author": item["expected"].get("author"), "isbn": item["expected"].get("isbn")},
    )
    out = tmp_path / "results.json"
    ev.cmd_compare(
        Namespace(
            golden=str(tmp_path / "golden.json"),
            predictions=str(pred_path),
            eval_dir=str(tmp_path),
            out=str(out),
            no_write=False,
        )
    )
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["auto_import_allowed"] is False
    assert result["metrics"]["title_accuracy"] == 0.0
    assert result["misses"]
