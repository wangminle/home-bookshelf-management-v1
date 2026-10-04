#!/usr/bin/env python3
"""管理 CLI 评测运行器（BI-09，规划 §4.6/4.7）。

用后端已配置的 Owner 模型（llm_settings）对金标准测试集逐张跑视觉识别，
产出可复现的评测报告。属于受信任的本机运维入口，不是新增远程 Agent 权限。

安全边界（契约）：
- 显式 --database-url 指定后端数据库；API Key 由服务内部读取，不导出、
  不写报告、不打印；报告只含模型 ID、配置指纹与接口主机名；
- 评测不修改图书库（只读 llm_settings；书目零写入）；
- 按图片哈希 + 模型配置指纹 + 提示词版本缓存，同配置重跑命中缓存不再
  付费调用；请求结果未知（超时）如实注明潜在重复计费；
- 逐条写检查点（原子替换），中断后重跑从未完成条目继续（已完成的走缓存）；
- 费用：提供方 usage可得时汇总 token；价格未知时费用标 null（不填 0）。

用法：
    backend/.venv/bin/python scripts/run_cover_model_eval.py \
        --golden tests/eval/user-set/golden.json \
        --database-url sqlite:///backend/data/bookshelf.db \
        --data-dir backend/data
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import eval_golden as eg  # noqa: E402


def _die(msg: str) -> int:
    print(f"错误: {msg}", file=sys.stderr)
    return 1


def _compact_now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ── 可注入薄封装（测试用假对象替换，不触后端）──

def load_settings_row(db) -> Any:
    from app.services.llm_settings import load_row

    return load_row(db)


def run_recognition(db, image_path: Path, *, use_cache: bool, cache_dir: Path | None):
    from app.services.vision import recognize_cover_fields

    return recognize_cover_fields(db, image_path, use_cache=use_cache, cache_dir=cache_dir)


def _safe_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    try:
        parts = urlsplit(base_url)
        return f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        return None


# ── 作者列表口径（BUG-279）──

# 连接词/短语（"A and B" / "A et al."）也是作者分隔信号，但不切西文人名
# 内部的空白——"Yuval Noah Harari" 是一位作者，不是三位。
_AUTHOR_CONNECTORS = re.compile(r"\s+(?:et\s+al\.?|and)\s+", re.IGNORECASE)


def _expected_authors(value: Any) -> list[str]:
    """金标准作者列表 → 规范化人名数组（只按真实作者分隔符切分）。

    与 ecr._author_tokens 不同：那里会把西文全名再按空白拆成单词，token 数
    无法判断作者人数（单个人名也 ≥2 token，误入多作者口径）。作者基数只看
    分隔符切出的段数，人名内部空白整体归一化后比较。
    """
    import eval_cover_recognition as ecr

    if isinstance(value, list):
        parts = [str(v) for v in value]
    else:
        parts = ecr.AUTHOR_SEPARATORS.split(_AUTHOR_CONNECTORS.sub("、", str(value or "")))
    return [folded for p in parts
            if (folded := ecr.normalize_text(p))]


def _match_row(entry: dict, result) -> dict:
    """单条匹配结论：复用既有 compare 判定（兼容口径），补充多作者完整性。"""
    import eval_cover_recognition as ecr

    expected = entry.get("expected") or {}
    predicted = result.candidate if (result and result.ok and result.candidate) else None
    pred_title = (predicted.title or "") if predicted else ""
    pred_authors = (predicted.authors or []) if predicted else []
    pred_isbn = predicted.isbn if predicted else None

    match: dict[str, Any] = {}
    if expected.get("title"):
        match["title"] = ecr.title_matches(pred_title, expected["title"])
    if expected.get("author"):
        match["author"] = ecr.author_matches(
            "、".join(pred_authors) if pred_authors else "", expected["author"])
    if expected.get("isbn"):
        match["isbn"] = bool(pred_isbn) and ecr.isbn_matches(pred_isbn, expected["isbn"])

    match["book_level"] = None
    parts = []
    if "title" in match:
        parts.append(match["title"])
    if "author" in match:
        parts.append(match["author"])
    if "isbn" in match:
        parts.append(match["isbn"])
    if parts:
        match["book_level"] = all(parts)

    # 多作者完整性（严格）：期望多作者且封面署作者时，预测须覆盖全部期望作者。
    # BUG-279：作者基数按真实分隔符切分判断（西文全名的空白不拆），
    # 单作者书名（如 "Yuval Noah Harari"）不得误入多作者口径。
    expected_authors = _expected_authors(expected.get("author")) if expected.get("author") else []
    if len(expected_authors) >= 2 and entry.get("authors_on_cover", True):
        pred_set = {ecr.normalize_text(a) for a in pred_authors}
        match["multi_author_complete"] = all(a in pred_set for a in expected_authors)
    return match


def _progress(rows: list[dict], picked: list[dict], entries_total: int,
              entries_groups: set) -> dict:
    """完成度口径（BUG-258）：区分计划样本 / 已完成样本 / 已完成且已核验样本。

    sample（golden_stats 全集口径）只描述测试集本身，不代表本次评测覆盖；
    门控必须读本字段。计划数以**本次评测应完成的全部金标准样本**计
    （limit 截断 planned=全集/completed=子集 → complete=False，冒烟报告
    无法过门控）。group 口径与 golden_stats 一致：组内任一照片
    verified 即计为已核验组。
    """
    completed_groups = {r.get("group") for r in rows if r.get("group") is not None}
    completed_verified = {r.get("group") for r in rows
                          if r.get("verified") and r.get("group") is not None}
    planned_photos = entries_total
    completed_photos = len(rows)
    return {
        "planned_photos": planned_photos,
        "completed_photos": completed_photos,
        "planned_groups": len(entries_groups),
        "completed_groups": len(completed_groups),
        "completed_verified_groups": len(completed_verified),
        # 未完成（limit 截断/中断恢复的部分结果）与冒烟报告据此被门控拒绝
        "complete": completed_photos >= planned_photos > 0,
        "smoke": len(picked) < entries_total,
    }


def _verified_book_level(rows: list[dict]) -> tuple[float | None, int, int]:
    """门控口径（BUG-259）：仅由实际完成且人工核验的**不同书**计算书级准确率。

    未核验样本不进入分子分母，不能稀释错误。每组（书）取全部已评测照片：
    组内任一照片 book_level 未通过则该书计错。
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("verified") and r.get("group") is not None:
            groups.setdefault(r["group"], []).append(r)
    total = len(groups)
    if not total:
        return None, 0, 0
    ok = sum(1 for rs in groups.values()
             if all(r["match"].get("book_level") is True for r in rs))
    return round(ok / total, 4), ok, total


def _metrics(entries_rows: list[dict]) -> dict:
    def _rate(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    title_n = sum(1 for r in entries_rows if r["match"].get("title") is not None)
    title_ok = sum(1 for r in entries_rows if r["match"].get("title") is True)
    author_n = sum(1 for r in entries_rows if r["match"].get("author") is not None)
    author_ok = sum(1 for r in entries_rows if r["match"].get("author") is True)
    isbn_n = sum(1 for r in entries_rows if r["match"].get("isbn") is not None)
    isbn_ok = sum(1 for r in entries_rows if r["match"].get("isbn") is True)
    bl_n = sum(1 for r in entries_rows if r["match"].get("book_level") is not None)
    bl_ok = sum(1 for r in entries_rows if r["match"].get("book_level") is True)
    ma_n = sum(1 for r in entries_rows if r["match"].get("multi_author_complete") is not None)
    ma_ok = sum(1 for r in entries_rows if r["match"].get("multi_author_complete") is True)
    # ISBN 准确率：模型给出了 ISBN 的条目里，正确的比例（幻觉度量）
    gave_isbn = [r for r in entries_rows if r.get("predicted_isbn")]
    isbn_gave_ok = sum(1 for r in gave_isbn if r["match"].get("isbn") is True)
    # ISBN 召回：封面确有 ISBN（isbn_visible）的条目里读对的 proportion
    visible = [r for r in entries_rows if r.get("isbn_visible")]
    isbn_recall_ok = sum(1 for r in visible if r["match"].get("isbn") is True)
    # 门控口径（BUG-259）：仅实际完成且人工核验的不同书；未核验样本不进分子分母
    bl_verified, bl_verified_ok, bl_verified_n = _verified_book_level(entries_rows)

    return {
        "title_accuracy": _rate(title_ok, title_n),
        "author_accuracy_any_hit": _rate(author_ok, author_n),
        "book_level_accuracy": _rate(bl_ok, bl_n),
        "book_level_accuracy_verified": bl_verified,
        "multi_author_completeness": _rate(ma_ok, ma_n),
        "isbn_accuracy_among_predicted": _rate(isbn_gave_ok, len(gave_isbn)),
        "isbn_recall_among_visible": _rate(isbn_recall_ok, len(visible)),
        "counts": {
            "title_evaluated": title_n, "author_evaluated": author_n,
            "isbn_evaluated": isbn_n, "book_level_evaluated": bl_n,
            "multi_author_evaluated": ma_n, "isbn_predicted": len(gave_isbn),
            "isbn_visible": len(visible),
            "book_level_verified_evaluated": bl_verified_n,
            "book_level_verified_correct": bl_verified_ok,
            # 任一作者命中为兼容口径，不得用它宣称多作者全部正确
            "note_compat": "author_accuracy_any_hit 沿用任一作者命中口径（兼容）",
            "note_gate": "book_level_accuracy_verified 仅含实际完成且人工核验的不同书（门控口径）",
        },
        "thresholds": {"title": 0.90, "author": 0.80, "book_level": 0.75},
    }


def run_eval(
    entries: list[dict],
    *,
    db,
    row,
    golden_path: Path,
    out_path: Path,
    use_cache: bool = True,
    cache_dir: Path | None = None,
    limit: int | None = None,
    run_fn=run_recognition,
) -> dict:
    from app.services import vision as vision_module

    image_root = golden_path.parent / "covers"
    picked = entries[:limit] if limit else entries
    golden_total = len(entries)
    started = time.monotonic()

    stats = eg.golden_stats(entries)
    identity = {
        "model_fingerprint": vision_module.model_fingerprint(row),
        "prompt_version": vision_module.PROMPT_VERSION,
        "task_type": vision_module.TASK_TYPE,
        "model_id": row.model_id,
        "base_url_host": _safe_host(row.base_url),
        "golden_sha256": eg.golden_sha256(entries),
        "golden_path": str(golden_path),
    }

    rows: list[dict] = []
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "entries_with_usage": 0}
    failures: dict[str, int] = {}

    def _checkpoint() -> None:
        report["entries"] = rows
        report["metrics"] = _metrics(rows)
        report["progress"] = _progress(rows, picked, golden_total,
                                       {e.get("group") for e in entries
                                        if e.get("group") is not None})
        report["usage_totals"] = dict(usage_totals,
                                      cost=None, cost_status="unknown",
                                      cost_note="价格未知时费用如实为 null，不填 0")
        report["failures_by_code"] = failures
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, out_path)

    report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "runner": "run_cover_model_eval",
        "identity": identity,
        "config": {"timeout_seconds": row.timeout_seconds, "max_tokens": row.max_tokens,
                   "temperature": row.temperature, "image_detail": row.image_detail,
                   "use_cache": use_cache},
        "sample": stats,
    }

    for i, entry in enumerate(picked, 1):
        image = image_root / entry["file"]
        result = run_fn(db, image, use_cache=use_cache, cache_dir=cache_dir)
        predicted = result.candidate if (result.ok and result.candidate) else None
        row_out = {
            "id": entry["id"], "file": entry["file"], "group": entry["group"],
            "difficulty": entry.get("difficulty"), "verified": bool(entry.get("verified")),
            "isbn_visible": bool(entry.get("isbn_visible")),
            "expected": entry.get("expected"),
            "ok": result.ok, "error_code": result.error_code,
            "elapsed_ms": result.elapsed_ms, "cache_hit": result.cache_hit,
            "attempts": result.attempts, "usage": result.usage,
            "predicted_isbn": predicted.isbn if predicted else None,
            "predicted": ({"title": predicted.title, "subtitle": predicted.subtitle,
                           "authors": predicted.authors, "isbn": predicted.isbn,
                           "confidence": predicted.confidence,
                           "readable": {k: f.readable for k, f in predicted.fields.items()},
                           "warnings": predicted.warnings} if predicted else None),
            "message": result.message if not result.ok else "",
        }
        row_out["match"] = _match_row(entry, result)
        rows.append(row_out)

        if not result.ok and result.error_code:
            failures[result.error_code] = failures.get(result.error_code, 0) + 1
        if result.usage:
            usage_totals["entries_with_usage"] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = result.usage.get(key)
                if isinstance(value, int):
                    usage_totals[key] += value

        mark = "✔" if result.ok else f"✗[{result.error_code}]"
        print(f"  [{i}/{len(picked)}] {mark} {entry['file']}"
              + (f"（缓存）" if result.cache_hit else ""))
        _checkpoint()

    _checkpoint()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="视觉识书评测运行器（只读后端模型配置；不修改图书库；API Key 不导出）")
    parser.add_argument("--golden", default=str(PROJECT_ROOT / "tests/eval/user-set/golden.json"))
    parser.add_argument("--database-url", required=True,
                        help="后端数据库（如 sqlite:///backend/data/bookshelf.db）；只读 llm_settings")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "backend/data"),
                        help="缓存目录根（<data-dir>/vision_cache），与后端一致以复用缓存")
    parser.add_argument("--out", default=None, help="报告输出（默认 <金标准目录>/results-<时间戳>.json）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟）")
    parser.add_argument("--no-cache", action="store_true", help="绕过缓存强制重新调用（会付费）")
    parser.add_argument("--only-verified", action="store_true",
                        help="只评测人工核验过的条目（默认全量，样本核验状态入报告）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    golden_path = Path(args.golden).expanduser()
    try:
        entries = eg.load_golden(golden_path)
    except eg.GoldenError as exc:
        return _die(str(exc))
    if args.only_verified:
        entries = [e for e in entries if e.get("verified")]
        if not entries:
            return _die("没有 verified=true 的条目；先人工核对金标准（BI-08 流程）")

    # 环境先于 app 导入：DATA_DIR 决定缓存目录，DATABASE_URL 仅作提示不生效
    # （连接串显式传入，不经 settings，避免误连开发库）
    os.environ.setdefault("DATA_DIR", str(Path(args.data_dir).resolve()))
    if str(PROJECT_ROOT / "backend") not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT / "backend"))

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    try:
        engine = create_engine(args.database_url)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            row = load_settings_row(db)
            db.rollback()  # 只读：立即释放读事务
        except Exception as exc:  # noqa: BLE001 —— 连不上/表不存在给可操作提示
            db.close()
            return _die(f"无法读取后端模型配置: {exc}（确认 --database-url 且已执行 alembic upgrade head）")
    except Exception as exc:  # noqa: BLE001
        return _die(f"无法连接后端数据库: {exc}")

    if row is None or not getattr(row, "enabled", False):
        db.close()
        return _die("后端未启用视觉模型（Owner 在 /llm-settings 启用后再评测）")
    if not (row.base_url and row.model_id and row.api_key):
        db.close()
        return _die("后端模型配置不完整（缺接口地址/模型 ID/API Key）")

    out_path = (Path(args.out).expanduser() if args.out
                else golden_path.parent / f"results-{_compact_now()}.json")

    cache_dir = Path(args.data_dir).expanduser() / "vision_cache"
    print(f"评测 {len(entries)} 张照片（金标准 {golden_path}）")
    print(f"模型 {row.model_id}；报告 {out_path}")
    try:
        report = run_eval(entries, db=db, row=row, golden_path=golden_path,
                          out_path=out_path, use_cache=not args.no_cache,
                          cache_dir=cache_dir, limit=args.limit)
    finally:
        db.close()

    m = report["metrics"]
    print("\n指标:")
    for key in ("title_accuracy", "author_accuracy_any_hit", "book_level_accuracy",
                "book_level_accuracy_verified",
                "multi_author_completeness", "isbn_accuracy_among_predicted",
                "isbn_recall_among_visible"):
        value = m.get(key)
        print(f"  {key:34} {value if value is not None else '—'}")
    s = report["sample"]
    p = report.get("progress") or {}
    print(f"样本: {s['photos_total']} 照片 / {s['distinct_groups']} 本书"
          f"（已核验 {s['verified_groups']} 本；金标准全集口径）")
    print(f"完成: 计划 {p.get('planned_photos')} 张 / 已完成 {p.get('completed_photos')} 张，"
          f"已完成不同书 {p.get('completed_groups')} 本"
          f"（其中已核验 {p.get('completed_verified_groups')} 本）"
          f"{'，完整' if p.get('complete') else '，未完成/冒烟——禁止作为自动确认依据'}")
    if report["failures_by_code"]:
        print(f"失败分布: {json.dumps(report['failures_by_code'], ensure_ascii=False)}")
    print(f"费用: 未知（价格未配置；token 用量见报告 usage_totals，不填 0）")
    print(f"报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
