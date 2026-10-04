"""BI-09 回归：评测运行器（检查点、身份三元组、usage/费用口径、缓存复用）。

全部注入假识别函数（VisionServiceResult），不触后端数据库、不触网络。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import run_cover_model_eval as runner  # noqa: E402
from app.services.vision import (  # noqa: E402
    PROMPT_VERSION,
    TASK_TYPE,
    VisionCandidate,
    VisionField,
    VisionServiceResult,
    model_fingerprint,
)
from app.models import LlmSettings  # noqa: E402
import eval_golden as eg  # noqa: E402


ROW = LlmSettings(enabled=True, display_name="t", base_url="https://api.example.com/v1",
                  model_id="gpt-test", api_key="sk-test", timeout_seconds=5,
                  max_tokens=256, temperature=0.0, image_detail="auto")


def _candidate(title=None, authors=None, isbn=None, confidence=None) -> VisionCandidate:
    fields = {name: VisionField(value=value)
              for name, value in (("title", title), ("subtitle", None), ("isbn", isbn))}
    fields["authors"] = VisionField()
    return VisionCandidate(title=title, authors=authors or [], isbn=isbn,
                           fields=fields, confidence=confidence)


def _ok(candidate, usage=None, cache_hit=False, attempts=1) -> VisionServiceResult:
    return VisionServiceResult(ok=True, candidate=candidate, message="识别完成",
                               elapsed_ms=123.4, cache_hit=cache_hit, attempts=attempts,
                               usage=usage, model_fingerprint=model_fingerprint(ROW),
                               image_sha256="ab" * 32)


def _fail(code, message="x") -> VisionServiceResult:
    return VisionServiceResult(ok=False, error_code=code, message=message,
                               elapsed_ms=50.0, attempts=3,
                               model_fingerprint=model_fingerprint(ROW))


def _entries(tmp_path: Path) -> list[dict]:
    golden = tmp_path / "golden.json"
    golden.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {"id": "a", "file": "a.png", "difficulty": "normal", "group": "g1",
         "verified": True, "isbn_visible": False, "authors_on_cover": True,
         "expected": {"title": "三体", "author": "刘慈欣", "isbn": None}},
        {"id": "b", "file": "b.png", "difficulty": "art_font", "group": "g2",
         "verified": False, "isbn_visible": False, "authors_on_cover": True,
         "expected": {"title": "雾中灯塔", "author": "陈予安、林晚", "isbn": None}},
        {"id": "c", "file": "c.png", "difficulty": "angle", "group": "g2",
         "verified": False, "isbn_visible": True, "authors_on_cover": True,
         "expected": {"title": "拾光的人", "author": "周衡", "isbn": "9787553820217"}},
    ]
    golden.write_text(json.dumps(entries), encoding="utf-8")
    covers = tmp_path / "covers"
    covers.mkdir(exist_ok=True)
    for e in entries:
        (covers / e["file"]).write_bytes(b"png")
    return eg.load_golden(golden)


def _run(tmp_path, fake_results, *, limit=None):
    entries = _entries(tmp_path)
    calls: list[Path] = []

    def fake_run(db, image, *, use_cache, cache_dir):
        calls.append(image)
        return fake_results[len(calls) - 1]

    out = tmp_path / "results-test.json"
    report = runner.run_eval(entries, db=None, row=ROW, golden_path=tmp_path / "golden.json",
                             out_path=out, cache_dir=tmp_path / "cache", limit=limit,
                             run_fn=fake_run)
    return report, out, calls


def test_report_has_identity_triplet_and_sample(tmp_path):
    report, out, calls = _run(tmp_path, [
        _ok(_candidate("三体", ["刘慈欣"], confidence=0.9),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        _ok(_candidate("雾中灯塔", ["陈予安"])),
        _ok(_candidate("拾光的人", ["周衡"], isbn="9787553820217")),
    ])
    assert len(calls) == 3
    identity = report["identity"]
    assert identity["model_fingerprint"] == model_fingerprint(ROW)
    assert identity["prompt_version"] == PROMPT_VERSION
    assert identity["task_type"] == TASK_TYPE
    assert identity["golden_sha256"] == eg.golden_sha256(report and _entries(tmp_path))
    assert identity["model_id"] == "gpt-test"
    assert identity["base_url_host"] == "https://api.example.com"
    assert "sk-test" not in json.dumps(report)  # API Key 绝不进报告
    assert report["sample"]["distinct_groups"] == 2
    assert report["sample"]["verified_groups"] == 1

    # 报告已原子写出
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["identity"] == identity
    assert len(saved["entries"]) == 3


def test_metrics_and_usage_totals(tmp_path):
    report, _, _ = _run(tmp_path, [
        _ok(_candidate("三体", ["刘慈欣"]),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        # 漏了一个作者（多作者不完整）且书名对
        _ok(_candidate("雾中灯塔", ["陈予安"]),
            usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}),
        # ISBN 读错（校验位错被服务层丢弃 → predicted_isbn None）
        _ok(_candidate("拾光的人", ["周衡"], isbn=None)),
    ])
    m = report["metrics"]
    assert m["title_accuracy"] == 1.0
    assert m["author_accuracy_any_hit"] == 1.0  # 任一命中（兼容口径）
    assert m["multi_author_completeness"] == 0.0  # 严格口径拆开统计
    assert m["isbn_recall_among_visible"] == 0.0  # 封面有 ISBN 但没读出
    assert m["isbn_accuracy_among_predicted"] is None  # 没有给出任何 ISBN → 分母 0 为 None
    assert report["usage_totals"]["total_tokens"] == 43
    assert report["usage_totals"]["entries_with_usage"] == 2
    assert report["usage_totals"]["cost"] is None
    assert report["usage_totals"]["cost_status"] == "unknown"  # 费用未知不填 0


def test_failed_entries_counted_as_miss_with_error_code(tmp_path):
    report, out, _ = _run(tmp_path, [
        _fail("rate_limited"),
        _ok(_candidate("雾中灯塔", ["陈予安", "林晚"])),
        _ok(_candidate("拾光的人", ["周衡"], isbn="9787553820217")),
    ])
    assert report["failures_by_code"] == {"rate_limited": 1}
    m = report["metrics"]
    assert m["title_accuracy"] == round(2 / 3, 4)  # 失败=未识别，计入分母
    by_id = {r["id"]: r for r in report["entries"]}
    assert by_id["a"]["ok"] is False and by_id["a"]["error_code"] == "rate_limited"
    assert by_id["a"]["match"]["title"] is False
    assert by_id["b"]["match"]["multi_author_complete"] is True


def test_limit_smoke_and_checkpoint_written(tmp_path):
    report, out, calls = _run(tmp_path, [
        _ok(_candidate("三体", ["刘慈欣"])),
    ], limit=1)
    assert len(calls) == 1
    assert report["sample"]["photos_total"] == 3  # 样本统计仍是全集口径
    assert not (tmp_path / "results-test.json.tmp").exists()


def test_cache_hits_reported(tmp_path):
    report, _, _ = _run(tmp_path, [
        _ok(_candidate("三体", ["刘慈欣"]), cache_hit=True),
        _ok(_candidate("雾中灯塔", ["陈予安、林晚"]), cache_hit=True),
        _ok(_candidate("拾光的人", ["周衡"], isbn="9787553820217"), cache_hit=True),
    ])
    assert all(r["cache_hit"] for r in report["entries"])


def test_match_row_semantics():
    entry = {"expected": {"title": "活着", "author": "余华", "isbn": "9787506365437"},
             "authors_on_cover": True}
    result = _ok(_candidate("活着", ["余华"], isbn="9787506365437"))
    match = runner._match_row(entry, result)
    assert match == {"title": True, "author": True, "isbn": True, "book_level": True}

    # 无期望 ISBN 时不评 ISBN
    entry2 = {"expected": {"title": "活着", "author": "余华", "isbn": None}}
    match2 = runner._match_row(entry2, result)
    assert "isbn" not in match2 and match2["book_level"] is True


# ── BUG-279 回归：多作者基数按真实分隔符判断，西文全名是一位作者 ──

def test_match_row_western_single_author_not_counted_multi(tmp_path):
    """期望/预测同为 "Yuval Noah Harari"（一位作者）：不得误入多作者口径。"""
    entry = {"expected": {"title": "Sapiens", "author": "Yuval Noah Harari", "isbn": None},
             "authors_on_cover": True}
    match = runner._match_row(entry, _ok(_candidate("Sapiens", ["Yuval Noah Harari"])))
    assert match["author"] is True
    assert "multi_author_complete" not in match


def test_match_row_genuine_multi_author_mismatch_still_detected(tmp_path):
    """真正的多作者漏识别仍判不完整；全部预测到则完整。"""
    entry = {"expected": {"title": "Sapiens", "author": "Yuval Noah Harari、Daniel Kahneman",
                          "isbn": None},
             "authors_on_cover": True}
    match = runner._match_row(entry, _ok(_candidate("Sapiens", ["Yuval Noah Harari"])))
    assert match["multi_author_complete"] is False

    match2 = runner._match_row(
        entry, _ok(_candidate("Sapiens", ["Yuval Noah Harari", "Daniel Kahneman"])))
    assert match2["multi_author_complete"] is True


def test_western_single_author_full_run_metrics(tmp_path):
    """端到端：西文单作者不误入多作者分母；真正的多作者漏识别仍计入。"""
    golden = tmp_path / "golden.json"
    entries = [
        {"id": "a", "file": "a.png", "difficulty": "foreign", "group": "g1",
         "verified": True, "isbn_visible": False, "authors_on_cover": True,
         "expected": {"title": "Sapiens", "author": "Yuval Noah Harari", "isbn": None}},
        {"id": "b", "file": "b.png", "difficulty": "normal", "group": "g2",
         "verified": True, "isbn_visible": False, "authors_on_cover": True,
         "expected": {"title": "思考，快与慢", "author": "Daniel Kahneman、Amos Tversky",
                      "isbn": None}},
    ]
    golden.write_text(json.dumps(entries), encoding="utf-8")
    (tmp_path / "covers").mkdir(exist_ok=True)
    for e in entries:
        (tmp_path / "covers" / e["file"]).write_bytes(b"png")
    loaded = eg.load_golden(golden)

    def fake_run(db, image, *, use_cache, cache_dir):
        # a：西文全名整体命中；b：多作者只命中一位 → 不完整
        return _ok(_candidate("Sapiens", ["Yuval Noah Harari"])) if image.name == "a.png" \
            else _ok(_candidate("思考，快与慢", ["Daniel Kahneman"]))

    report = runner.run_eval(loaded, db=None, row=ROW, golden_path=golden,
                             out_path=tmp_path / "results-test.json",
                             cache_dir=tmp_path / "cache", run_fn=fake_run)
    by_id = {r["id"]: r for r in report["entries"]}
    assert by_id["a"]["match"]["author"] is True
    assert "multi_author_complete" not in by_id["a"]["match"]  # 单作者不进多作者口径
    assert by_id["b"]["match"]["multi_author_complete"] is False
    m = report["metrics"]
    assert m["counts"]["multi_author_evaluated"] == 1  # 只有真正的多作者条目
    assert m["multi_author_completeness"] == 0.0


# ── BUG-258/259 回归：完成度口径与已核验门控口径 ──

def test_progress_distinguishes_planned_completed_verified(tmp_path):
    """limit=1 冒烟：progress 记计划 3/完成 1，complete=False；全集口径 sample 不变。"""
    report, out, calls = _run(tmp_path, [
        _ok(_candidate("三体", ["刘慈欣"])),
    ], limit=1)
    p = report["progress"]
    assert p["planned_photos"] == 3 and p["completed_photos"] == 1
    assert p["complete"] is False
    assert p["smoke"] is True
    # 检查点里的进度与内存一致（中断恢复的部分结果可被门控识别拒绝）
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["progress"]["complete"] is False
    assert saved["progress"]["completed_photos"] == 1
    # 全集口径 sample 仍描述测试集本身（3 张照片 / 1 本已核验）
    assert report["sample"]["photos_total"] == 3
    assert report["sample"]["verified_groups"] == 1


def test_full_run_progress_complete(tmp_path):
    report, out, _ = _run(tmp_path, [
        _ok(_candidate("三体", ["刘慈欣"])),
        _ok(_candidate("雾中灯塔", ["陈予安", "林晚"])),
        _ok(_candidate("拾光的人", ["周衡"], isbn="9787553820217")),
    ])
    p = report["progress"]
    assert p["planned_photos"] == 3 and p["completed_photos"] == 3
    assert p["planned_groups"] == 2 and p["completed_groups"] == 2
    assert p["completed_verified_groups"] == 1  # 仅 g1（a 已核验）
    assert p["complete"] is True and p["smoke"] is False
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["progress"]["complete"] is True


def test_verified_gate_caliber_not_diluted_by_unverified(tmp_path):
    """BUG-259：已核验书（g1）全错 + 未核验（g2）全对——全集口径 2/3 虚高，
    门控口径 book_level_accuracy_verified 必须只算已核验书 = 0。"""
    report, _, _ = _run(tmp_path, [
        _ok(_candidate("错误的标题", ["刘慈欣"])),            # a：已核验，书名错
        _ok(_candidate("雾中灯塔", ["陈予安", "林晚"])),      # b：未核验，对
        _ok(_candidate("拾光的人", ["周衡"], isbn="9787553820217")),  # c：未核验，对
    ])
    m = report["metrics"]
    assert m["book_level_accuracy"] == round(2 / 3, 4)      # 全集口径（参考）
    assert m["book_level_accuracy_verified"] == 0.0         # 门控口径：不被未核验样本稀释
    assert m["counts"]["book_level_verified_evaluated"] == 1
    assert m["counts"]["book_level_verified_correct"] == 0


def test_verified_gate_caliber_counts_distinct_books(tmp_path):
    """同一本已核验书的多张照片按一组计（不同书口径），组内任一错则该书错。"""
    entries = [
        {"id": "a1", "file": "a1.png", "difficulty": "normal", "group": "g1",
         "verified": True, "isbn_visible": False, "authors_on_cover": True,
         "expected": {"title": "三体", "author": "刘慈欣", "isbn": None}},
        {"id": "a2", "file": "a2.png", "difficulty": "normal", "group": "g1",
         "verified": True, "isbn_visible": False, "authors_on_cover": True,
         "expected": {"title": "三体", "author": "刘慈欣", "isbn": None}},
    ]
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps(entries), encoding="utf-8")
    (tmp_path / "covers").mkdir(exist_ok=True)
    for e in entries:
        (tmp_path / "covers" / e["file"]).write_bytes(b"png")
    loaded = eg.load_golden(golden)

    calls: list[Path] = []

    def fake_run(db, image, *, use_cache, cache_dir):
        calls.append(image)
        # a1 对、a2 错：同一组 → 该书计错
        return _ok(_candidate("三体", ["刘慈欣"])) if len(calls) == 1 \
            else _ok(_candidate("别的书", ["刘慈欣"]))

    report = runner.run_eval(loaded, db=None, row=ROW, golden_path=golden,
                             out_path=tmp_path / "results-test.json",
                             cache_dir=tmp_path / "cache", run_fn=fake_run)
    assert report["progress"]["completed_verified_groups"] == 1
    assert report["metrics"]["book_level_accuracy_verified"] == 0.0


def test_verified_gate_caliber_none_when_no_verified_rows(tmp_path):
    """没有已完成且已核验样本时门控口径为 None（门控据此拒绝，不回落全集口径）。

    已核验但识别失败的条目计为错（计入已核验分母），所以要拿到 None 须
    测试集中完全没有已核验条目。
    """
    golden = tmp_path / "golden.json"
    entries = [
        {"id": "a", "file": "a.png", "difficulty": "normal", "group": "g1",
         "verified": False, "isbn_visible": False, "authors_on_cover": True,
         "expected": {"title": "三体", "author": "刘慈欣", "isbn": None}},
    ]
    golden.write_text(json.dumps(entries), encoding="utf-8")
    (tmp_path / "covers").mkdir(exist_ok=True)
    (tmp_path / "covers" / "a.png").write_bytes(b"png")
    loaded = eg.load_golden(golden)

    def fake_run(db, image, *, use_cache, cache_dir):
        return _ok(_candidate("三体", ["刘慈欣"]))

    report = runner.run_eval(loaded, db=None, row=ROW, golden_path=golden,
                             out_path=tmp_path / "results-test.json",
                             cache_dir=tmp_path / "cache", run_fn=fake_run)
    m = report["metrics"]
    assert m["book_level_accuracy"] == 1.0          # 全集口径照常
    assert m["book_level_accuracy_verified"] is None  # 门控口径无已核验样本
    assert m["counts"]["book_level_verified_evaluated"] == 0
    assert report["progress"]["completed_verified_groups"] == 0
