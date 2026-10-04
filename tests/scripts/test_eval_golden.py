"""BI-08 回归：金标准 v2 字段、分组统计、按组切分不泄漏。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import eval_golden as eg  # noqa: E402

USER_SET_GOLDEN = PROJECT_ROOT / "tests" / "eval" / "user-set" / "golden.json"


def test_user_set_golden_loads_with_v2_fields():
    entries = eg.load_golden(USER_SET_GOLDEN)
    assert len(entries) == 6
    for e in entries:
        assert e["group"] and isinstance(e["verified"], bool)
        assert "isbn_visible" in e and "authors_on_cover" in e
    by_id = {e["id"]: e for e in entries}
    # 同书多照片同组（sim_004 与 sim_006 是同一本书的两张照片）
    assert by_id["user-sim-004"]["group"] == by_id["user-sim-006"]["group"]
    assert by_id["user-sim-004"]["group"] != by_id["user-sim-005"]["group"]
    # 困难类别覆盖：isbn_visible 样例存在
    assert by_id["user-sim-005"]["isbn_visible"] is True


def test_golden_stats_counts_groups_not_photos():
    entries = eg.load_golden(USER_SET_GOLDEN)
    stats = eg.golden_stats(entries)
    assert stats["photos_total"] == 6
    assert stats["distinct_groups"] == 5          # sim_004/006 同组
    assert stats["groups_with_multiple_photos"] == 1
    assert stats["verified_groups"] == 0          # 全部未人工核验
    assert stats["isbn_visible_photos"] == 1
    assert stats["by_difficulty"]["normal"] == 2


def test_v1_entries_backward_compatible_group_defaults_to_id(tmp_path):
    v1 = [{"id": "a", "file": "a.jpg", "task": "cover_title_author",
           "difficulty": "normal", "expected": {"title": "x", "author": None, "isbn": None}},
          {"id": "b", "file": "b.jpg", "expected": {"title": "y"}}]
    p = tmp_path / "golden.json"
    p.write_text(json.dumps(v1), encoding="utf-8")
    entries = eg.load_golden(p)
    assert [e["group"] for e in entries] == ["a", "b"]  # 一图一组
    assert entries[1]["task"] == "cover_title_author"   # 缺省补默认
    assert entries[0]["verified"] is False


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "golden.json"
    p.write_text(json.dumps([
        {"id": "a", "file": "a.jpg", "expected": {"title": "x"}},
        {"id": "a", "file": "b.jpg", "expected": {"title": "y"}},
    ]), encoding="utf-8")
    with pytest.raises(eg.GoldenError, match="重复 id"):
        eg.load_golden(p)


def test_missing_title_rejected(tmp_path):
    p = tmp_path / "golden.json"
    p.write_text(json.dumps([{"id": "a", "file": "a.jpg", "expected": {"author": "x"}}]),
                 encoding="utf-8")
    with pytest.raises(eg.GoldenError, match="expected.title"):
        eg.load_golden(p)


def test_not_a_list_rejected(tmp_path):
    p = tmp_path / "golden.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(eg.GoldenError, match="非空 JSON 数组"):
        eg.load_golden(p)
    assert not (tmp_path / "missing.json").exists() or True
    with pytest.raises(eg.GoldenError):
        eg.load_golden(tmp_path / "missing.json")


def test_golden_sha_ignores_verified_flag():
    entries = eg.load_golden(USER_SET_GOLDEN)
    sha_before = eg.golden_sha256(entries)
    for e in entries:
        e["verified"] = True  # 人工核验不改变测试集版本指纹
    assert eg.golden_sha256(entries) == sha_before
    entries[0]["expected"]["title"] = "改动"
    assert eg.golden_sha256(entries) != sha_before


def test_split_by_group_never_leaks_same_book():
    entries = eg.load_golden(USER_SET_GOLDEN)
    calibration, validation = eg.split_by_group(entries, validation_ratio=0.34)
    assert set(calibration) & set(validation) == set()
    assert set(calibration) | set(validation) == {e["id"] for e in entries}
    # 同组（sim_004/006）必须整体落在同一侧
    group_of = {e["id"]: e["group"] for e in entries}
    sides = {}
    for i in calibration:
        sides[i] = "cal"
    for i in validation:
        sides[i] = "val"
    for id_a, id_b in (("user-sim-004", "user-sim-006"),):
        assert sides[id_a] == sides[id_b]
    # 同种子可复现
    again = eg.split_by_group(entries, validation_ratio=0.34)
    assert again == (calibration, validation)


def test_split_rejects_bad_ratio():
    entries = eg.load_golden(USER_SET_GOLDEN)
    with pytest.raises(eg.GoldenError):
        eg.split_by_group(entries, validation_ratio=1.5)


def test_verified_stats_flow():
    entries = eg.load_golden(USER_SET_GOLDEN)
    for e in entries:
        if e["group"] == "g-sim-004":  # 整组核验
            e["verified"] = True
    stats = eg.golden_stats(entries)
    assert stats["verified_groups"] == 1
    assert stats["verified_photos"] == 2
