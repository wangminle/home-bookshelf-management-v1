"""eval_cover_recognition.py 单元测试：归一化、字段对比、指标计算与门控结论。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import eval_cover_recognition as ecr  # noqa: E402


# ── 归一化与字段对比 ──

def test_normalize_text_fullwidth_case_punct():
    assert ecr.normalize_text(" Ｓａｐｉｅｎｓ：A Brief History! ") == "sapiensabriefhistory"
    assert ecr.normalize_text("三体（Ⅲ）") == "三体iii"


def test_title_matches():
    assert ecr.title_matches("三体", "三体 ")
    assert ecr.title_matches("Sapiens", "ＳＡＰＩＥＮＳ")
    assert not ecr.title_matches("三体", "三體")  # 简繁不视为相同
    assert not ecr.title_matches("三体", None)


def test_author_matches_overlap_counts():
    assert ecr.author_matches("刘慈欣", "刘慈欣")
    assert ecr.author_matches("刘慈欣", "刘慈欣、姚海军")   # 多作者部分命中算对
    assert ecr.author_matches("Yuval Noah Harari", "Harari")  # 西文姓氏单独命中算对
    assert not ecr.author_matches("余华", "刘慈欣")
    assert not ecr.author_matches(None, "刘慈欣")


def test_isbn_10_13_equivalence():
    assert ecr.isbn_matches("9787020002207", "702000220X")  # ISBN-10 自动换算 13
    assert not ecr.isbn_matches("9787020002207", "9787506365437")
    assert not ecr.isbn_matches(None, "9787020002207")


def test_isbn_dirty_x_input_no_crash():
    """CHK-056：前 9 位含 X 的脏 ISBN 串不应让换算崩溃，记 miss 即可。"""
    assert ecr.isbn_matches("9787536692930", "7536692X33") is False
    assert ecr.isbn_matches("9787536692930", "7536692933")  # 正常 10 位仍换算命中
    assert ecr.normalize_isbn("70200022XX") == "70200022XX"  # 原样返回交比对


def test_book_level_requires_isbn_when_expected(tmp_path):
    """CHK-056：期望 ISBN 存在而预测错时，书名作者全对也不得计入书级（--yes 门控）。"""
    golden = write(tmp_path / "golden.json", [
        {"file": "a.jpg", "expected": {"title": "三体", "author": "刘慈欣", "isbn": "9787536692930"}},
        {"file": "b.jpg", "expected": {"title": "活着", "author": "余华"}},
    ])
    predictions = write(tmp_path / "predictions.json", {"entries": [
        {"file": "a.jpg", "predicted": {"title": "三体", "author": "刘慈欣", "isbn": "9787536692935"}},  # ISBN 错
        {"file": "b.jpg", "predicted": {"title": "活着", "author": "余华"}},
    ]})
    out = tmp_path / "results-i.json"
    ecr.main(["compare", "--golden", str(golden), "--predictions", str(predictions), "--out", str(out)])

    result = json.loads(out.read_text(encoding="utf-8"))
    m = result["metrics"]
    assert m["title_accuracy"] == 1.0
    assert m["isbn_correct"] == 0
    # 书级：a 因 ISBN 错不计入 → 1/2，未达 75% 门槛
    assert m["book_level_correct"] == 1 and m["book_level_accuracy"] == 0.5
    assert result["auto_import_allowed"] is False
    miss = result["misses"][0]
    assert miss["problems"] == ["isbn"]


# ── template / compare ──

def write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_template_generates_skeleton(tmp_path, capsys):
    golden = write(tmp_path / "golden.json", [
        {"file": "a.jpg", "expected": {"title": "三体", "author": "刘慈欣"}, "difficulty": "normal"},
        {"file": "b.jpg", "expected": {"title": "活着", "author": None, "isbn": None}},
    ])
    out = tmp_path / "predictions.json"
    ecr.main(["template", "--golden", str(golden), "--out", str(out)])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["model"] is None
    assert [e["file"] for e in data["entries"]] == ["a.jpg", "b.jpg"]
    assert data["entries"][0]["predicted"] == {"title": None, "author": None, "isbn": None}


def test_compare_metrics_misses_and_gate(tmp_path, capsys):
    golden = write(tmp_path / "golden.json", [
        {"file": "a.jpg", "expected": {"title": "三体", "author": "刘慈欣"}, "difficulty": "normal"},
        {"file": "b.jpg", "expected": {"title": "活着", "author": None}, "difficulty": "normal"},
        {"file": "c.jpg", "expected": {"title": "坚如磐石", "author": "孙颙"}, "difficulty": "art_font"},
        {"file": "d.jpg", "expected": {"title": "Sapiens", "author": "Yuval Noah Harari"}, "difficulty": "foreign"},
    ])
    predictions = write(tmp_path / "predictions.json", {"model": "test-vision", "entries": [
        {"file": "a.jpg", "predicted": {"title": "三体", "author": "刘慈欣"}},
        {"file": "b.jpg", "predicted": {"title": "活着", "author": "余华"}},      # 无期望作者，不扣分
        {"file": "c.jpg", "predicted": {"title": "磐石", "author": "孙颙"}},      # 书名错
        {"file": "d.jpg", "predicted": {"title": "Sapiens", "author": "George Orwell"}},  # 作者全错
        {"file": "extra.jpg", "predicted": {"title": "孤儿"}},
    ]})
    out = tmp_path / "results-x.json"
    rc = ecr.main(["compare", "--golden", str(golden), "--predictions", str(predictions), "--out", str(out)])

    assert rc == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    m = result["metrics"]
    assert m["title_correct"] == 3 and m["title_evaluated"] == 4
    # 作者明细：a 刘慈欣✔ c 孙颙✔；d 预测 "George Orwell" 与期望无任何词命中 → ✗
    # （部分姓名如 "Harari" 命中算对，见 test_author_matches_overlap_counts）
    assert m["author_correct"] == 2 and m["author_evaluated"] == 3
    # 书级 = title 对 且（无期望作者或作者对）：a✔ b✔(无期望作者) c✗ d✗ → 2/4
    assert m["book_level_correct"] == 2 and m["book_level_accuracy"] == 0.5
    assert result["auto_import_allowed"] is False
    assert result["model"] == "test-vision"
    assert set(result["by_difficulty"]) == {"normal", "art_font", "foreign"}
    miss = next(m0 for m0 in result["misses"] if m0["file"] == "c.jpg")
    assert miss["problems"] == ["title"] and miss["difficulty"] == "art_font"
    assert result["warnings"]["prediction_without_golden"] == ["extra.jpg"]

    stdout = capsys.readouterr().out
    assert "未达标" in stdout and "c.jpg" in stdout


def test_compare_all_correct_opens_gate(tmp_path, capsys):
    golden = write(tmp_path / "golden.json", [
        {"file": "a.jpg", "expected": {"title": "三体", "author": "刘慈欣"}},
        {"file": "b.jpg", "expected": {"title": "活着"}},
    ])
    predictions = write(tmp_path / "predictions.json", {"entries": [
        # 扁平格式也应被接受
        {"file": "a.jpg", "title": "三体", "author": "刘慈欣"},
        {"file": "b.jpg", "title": "活着"},
    ]})
    out = tmp_path / "results-y.json"
    rc = ecr.main(["compare", "--golden", str(golden), "--predictions", str(predictions), "--out", str(out)])

    assert rc == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["metrics"]["book_level_accuracy"] == 1.0
    assert result["auto_import_allowed"] is True
    assert "允许 --yes 自动入库" in capsys.readouterr().out


def test_compare_refuses_when_no_file_matched(tmp_path):
    golden = write(tmp_path / "golden.json", [
        {"file": "a.jpg", "expected": {"title": "三体"}},
    ])
    predictions = write(tmp_path / "predictions.json", {"entries": [
        {"file": "zzz.jpg", "predicted": {"title": "?"}},
    ]})
    with pytest.raises(SystemExit):
        ecr.main(["compare", "--golden", str(golden), "--predictions", str(predictions),
                  "--out", str(tmp_path / "r.json")])


def test_compare_difficulty_defaults_to_normal(tmp_path):
    golden = write(tmp_path / "golden.json", [
        {"file": "a.jpg", "expected": {"title": "三体", "author": "刘慈欣"}, "difficulty": "weird"},
    ])
    predictions = write(tmp_path / "predictions.json", {"entries": [
        {"file": "a.jpg", "predicted": {"title": "三体", "author": "刘慈欣"}},
    ]})
    out = tmp_path / "results-z.json"
    ecr.main(["compare", "--golden", str(golden), "--predictions", str(predictions), "--out", str(out)])
    result = json.loads(out.read_text(encoding="utf-8"))
    assert "normal" in result["by_difficulty"] and "weird" not in result["by_difficulty"]
