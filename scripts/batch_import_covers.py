#!/usr/bin/env python3
"""批量导入图书封面并建档。

已实施方案：design/achievements/批量导入图书封面并建档方案.md（路径① Agent + CLI）

流程：
    1. scan    扫描封面目录，生成/合并 batch_manifest.json 骨架（status=pending）
    2. Agent   用视觉逐张识别封面，填 title/author/isbn（status: pending → recognized）
    3. 用户    核对清单（status: recognized → confirmed / skip）
    4. run     只对 confirmed 条目调 POST /books/intake（复用 BookshelfClient.add，
               认证/超时/查重全部继承），出报告并回写 manifest（imported / failed）

安全阈值（方案 §4.4）：--yes 自动确认模式要求最近一次 eval 的书级完全正确率 ≥ 75%，
否则拒绝执行（--force 可越过，仅限人工判断）。

用法：
    python3 scripts/batch_import_covers.py scan --dir ./covers
    python3 scripts/batch_import_covers.py status --manifest batch_manifest.json
    python3 scripts/batch_import_covers.py run --manifest batch_manifest.json --dry-run
    python3 scripts/batch_import_covers.py run --manifest batch_manifest.json

环境变量（同 CLI）：
    BOOKSHELF_API_URL   默认 http://127.0.0.1:8000
    BOOKSHELF_TOKEN     Agent Bearer Token（写接口必需）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "cli"))

from bookshelf.client import BookshelfClient  # noqa: E402

MANIFEST_VERSION = 1
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".tif", ".tiff"}

# 状态机：pending → recognized →（用户核对）confirmed / skip →（run）imported / failed
# failed 可通过 --retry-failed 重试；imported 为终态，重跑自动跳过。
VALID_STATUS = ("pending", "recognized", "confirmed", "skip", "imported", "failed")

# 方案 §4.4：书级完全正确率低于该值时禁止 --yes 自动入库
BOOK_LEVEL_GATE = 0.75


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _die(msg: str) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


# ── manifest 读写 ──

def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        _die(f"清单不存在: {path}（先执行 scan 生成）")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(f"清单不是合法 JSON: {path}（{exc}）")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        _die(f"清单格式错误: {path}（应为含 entries 数组的对象）")
    bad = [e.get("file", "?") for e in manifest["entries"] if e.get("status") not in VALID_STATUS]
    if bad:
        _die(f"清单含非法 status 的条目: {bad}（合法值 {VALID_STATUS}）")
    return manifest


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ── scan ──

def cmd_scan(args: argparse.Namespace) -> None:
    src = Path(args.dir).expanduser().resolve()
    if not src.is_dir():
        _die(f"封面目录不存在: {src}")

    images = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        _die(f"目录里没有图片（支持 {'/'.join(sorted(IMAGE_SUFFIXES))}）: {src}")

    manifest_path = Path(args.out).expanduser()
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        if manifest.get("source_dir") not in (None, str(src)):
            _die(f"清单属于其他目录 {manifest.get('source_dir')}，请换 --out 或先核对")
    else:
        manifest = {
            "version": MANIFEST_VERSION,
            "source_dir": str(src),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "entries": [],
        }

    existing = {e.get("file"): e for e in manifest["entries"] if e.get("file")}
    added, missing = [], []
    for rel in images:
        if rel not in existing:
            manifest["entries"].append(
                {"file": rel, "title": None, "author": None, "isbn": None,
                 "price": None, "channel": None, "location": None, "member_id": None,
                 "status": "pending", "note": None, "result": None}
            )
            added.append(rel)
    seen = set(images)
    for entry in manifest["entries"]:
        if entry.get("file") and entry["file"] not in seen:
            missing.append(entry["file"])

    save_manifest(manifest_path, manifest)
    print(f"清单: {manifest_path}")
    print(f"共 {len(manifest['entries'])} 条（新增 {len(added)}，目录中已移除 {len(missing)}）")
    if missing:
        for f in missing:
            print(f"  ⚠ 不在目录: {f}（条目保留，run 时会报文件缺失）")
    print("下一步: Agent 逐张识别封面填入 title/author，status 置为 recognized，再交用户核对。")


# ── status ──

def cmd_status(args: argparse.Namespace) -> None:
    manifest = load_manifest(Path(args.manifest))
    counts: dict[str, int] = {}
    for entry in manifest["entries"]:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    print(f"清单: {args.manifest}（目录 {manifest.get('source_dir')}）")
    for status in VALID_STATUS:
        if counts.get(status):
            print(f"  {status:10} {counts[status]}")
    need_fix = [e for e in manifest["entries"] if e["status"] in ("recognized", "confirmed") and not (e.get("isbn") or e.get("title"))]
    if need_fix:
        print("⚠ 以下条目缺 isbn 和 title，run 时会失败:")
        for e in need_fix:
            print(f"  {e['file']}")


# ── eval 阈值门控 ──

def latest_eval_result(eval_dir: Path) -> dict[str, Any] | None:
    results = sorted(eval_dir.glob("results-*.json"))
    if not results:
        return None
    try:
        return json.loads(results[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def check_auto_confirm_gate(eval_dir: Path, threshold: float) -> tuple[bool, str]:
    """--yes 模式前置检查：最近一次 eval 书级完全正确率须达标（方案 §4.4）。"""
    result = latest_eval_result(eval_dir)
    if result is None:
        return False, f"未找到 eval 结果（{eval_dir}/results-*.json），先跑 scripts/eval_cover_recognition.py"
    accuracy = (result.get("metrics") or {}).get("book_level_accuracy")
    if not isinstance(accuracy, (int, float)):
        return False, f"eval 结果缺少 metrics.book_level_accuracy: {result.get('generated_at')}"
    if accuracy < threshold:
        return False, f"最近 eval 书级完全正确率 {accuracy:.1%} 低于 {threshold:.0%}，须逐本人工确认（确认后置 confirmed 再 run）"
    return True, f"最近 eval 书级完全正确率 {accuracy:.1%}（{result.get('generated_at')}）"


# ── run ──

def _classify_response(resp: Any) -> dict[str, Any]:
    """把 BookshelfClient.add() 的返回归类为 created / exists。"""
    data = (resp or {}).get("data") if isinstance(resp, dict) else None
    data = data or {}
    book = data.get("book") or {}
    outcome = "exists" if data.get("already_exists") else "created"
    return {
        "outcome": outcome,
        "book_id": book.get("id"),
        "action": data.get("action"),
        "message": data.get("message"),
    }


def cmd_run(args: argparse.Namespace, client: BookshelfClient | None = None) -> int:
    manifest_path = Path(args.manifest).expanduser()
    manifest = load_manifest(manifest_path)
    source_dir = Path(manifest.get("source_dir") or ".")
    entries = manifest["entries"]

    statuses = [e["status"] for e in entries]
    target_status = {"confirmed"}
    if args.yes:
        target_status.add("recognized")
    if args.retry_failed:
        target_status.add("failed")
    todo = [e for e in entries if e["status"] in target_status]
    skipped = len(entries) - len(todo)

    if not todo:
        print(f"没有待入库条目（{statuses.count('confirmed')} confirmed / {statuses.count('recognized')} recognized）。")
        return 0

    if args.yes and not args.force:
        ok, msg = check_auto_confirm_gate(Path(args.eval_dir), BOOK_LEVEL_GATE)
        if ok:
            print(f"eval 门控通过: {msg}")
        elif args.dry_run:
            # dry-run 只是预览不入库，不拦；正式 run 仍会被拒绝
            print(f"⚠ --yes 门控未过: {msg}（dry-run 仅预览；正式 run 将被拒绝）")
        else:
            _die(f"--yes 被拒绝: {msg}（人工核对后不用 --yes；确要越过用 --force）")

    def effective(entry: dict[str, Any], key: str) -> Any:
        per_entry = entry.get(key)
        return per_entry if per_entry is not None else getattr(args, key, None)

    # 预检：缺识别键 / 缺文件的条目直接标 failed，不发请求
    preflight_failed: list[str] = []
    preflight_failed_ids: set[int] = set()
    for entry in todo:
        if not (entry.get("isbn") or entry.get("title")):
            entry["status"] = "failed"
            entry["result"] = {"outcome": "failed", "error": "缺少 isbn 和 title，无法入库"}
        elif entry.get("file") and not (source_dir / entry["file"]).is_file():
            entry["status"] = "failed"
            entry["result"] = {"outcome": "failed", "error": f"封面文件缺失: {entry['file']}"}
        else:
            continue
        preflight_failed.append(entry["file"] or "?")
        preflight_failed_ids.add(id(entry))
    todo = [e for e in todo if id(e) not in preflight_failed_ids]

    print(f"待入库 {len(todo)} 本（预检失败 {len(preflight_failed)}），跳过 {skipped} 条（非目标状态）")
    if preflight_failed:
        for f in preflight_failed:
            print(f"  ✗ 预检失败: {f}")

    if args.dry_run:
        print("\n[dry-run] 以下条目将被提交（不调 API）:")
        for i, entry in enumerate(todo, 1):
            label = entry.get("title") or entry.get("isbn") or "(无识别键)"
            img = entry["file"] or "(无封面图)"
            extras = []
            if effective(entry, "price") is not None:
                extras.append(f"price={effective(entry, 'price')}")
            if effective(entry, "channel"):
                extras.append(f"channel={effective(entry, 'channel')}")
            if effective(entry, "location"):
                extras.append(f"location={effective(entry, 'location')}")
            suffix = f"  [{', '.join(extras)}]" if extras else ""
            print(f"  [{i}/{len(todo)}] {label} / {entry.get('author') or '?'}  ({img}){suffix}")
        print("[dry-run] 未调用 API、未修改清单。")
        return 0

    if client is None:
        client = BookshelfClient()
    if not os.environ.get("BOOKSHELF_TOKEN"):
        print("⚠ 未设置 BOOKSHELF_TOKEN，写接口大概率被拒（401/403）。", file=sys.stderr)
    try:
        client.health()
    except RuntimeError as exc:
        _die(f"后端不可用: {exc}")

    results: list[dict[str, Any]] = []
    todo_ids = {id(e) for e in todo}
    for e in entries:
        if id(e) in todo_ids:
            continue  # 下面逐本处理
        if id(e) in preflight_failed_ids:
            # 仅本次预检失败计入 failed；历史遗留 failed 本次未触碰，应记 skipped（BUG-170）
            results.append({"file": e.get("file"), "title": e.get("title"), "author": e.get("author"),
                            "outcome": "failed", "book_id": None, "action": None,
                            "error": e["result"].get("error")})
        else:
            results.append({"file": e.get("file"), "title": e.get("title"), "author": e.get("author"),
                            "outcome": "skipped", "book_id": None, "action": None, "error": None})
    summary = {"created": 0, "exists": 0, "failed": len(preflight_failed)}
    started = time.monotonic()

    for i, entry in enumerate(todo, 1):
        label = entry.get("title") or entry.get("isbn") or entry.get("file")
        image = Path(source_dir / entry["file"]) if entry.get("file") else None
        try:
            resp = client.add(
                isbn=entry.get("isbn") or None,
                title=entry.get("title") or None,
                author=entry.get("author") or None,
                image=image,
                price=effective(entry, "price"),
                channel=effective(entry, "channel") or None,
                location=effective(entry, "location") or None,
                member_id=effective(entry, "member_id"),
            )
            info = _classify_response(resp)
            entry["status"] = "imported"
            entry["result"] = info
            summary[info["outcome"]] += 1
            mark = "✔ 已存在" if info["outcome"] == "exists" else "✔ 已入库"
            print(f"  [{i}/{len(todo)}] {mark}: {label} → ID {info['book_id']}")
        except RuntimeError as exc:
            entry["status"] = "failed"
            entry["result"] = {"outcome": "failed", "error": str(exc)}
            summary["failed"] += 1
            print(f"  [{i}/{len(todo)}] ✗ 失败: {label} — {exc}")
        results.append({
            "file": entry.get("file"), "title": entry.get("title"), "author": entry.get("author"),
            "outcome": entry["result"]["outcome"], "book_id": entry["result"].get("book_id"),
            "action": entry["result"].get("action"), "error": entry["result"].get("error"),
        })

    save_manifest(manifest_path, manifest)
    report = {
        "manifest": str(manifest_path),
        "generated_at": now_iso(),
        "duration_seconds": round(time.monotonic() - started, 1),
        "summary": {"total": len(todo) + len(preflight_failed), **summary, "skipped": skipped},
        "entries": sorted(results, key=lambda r: (r["outcome"], r.get("file") or "")),
    }
    report_path = Path(args.report).expanduser()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n完成: 入库 {summary['created']}，已存在 {summary['exists']}，失败 {summary['failed']}，跳过 {skipped}")
    print(f"报告: {report_path}")
    print(f"清单已回写: {manifest_path}")
    return 1 if summary["failed"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量导入图书封面并建档（Agent + CLI 路径）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描封面目录，生成/合并清单骨架")
    p_scan.add_argument("--dir", default="covers", help="封面图片目录（默认 ./covers）")
    p_scan.add_argument("--out", default="batch_manifest.json", help="清单输出路径")

    p_status = sub.add_parser("status", help="查看清单各状态计数与待修条目")
    p_status.add_argument("--manifest", default="batch_manifest.json")

    p_run = sub.add_parser("run", help="按清单调 POST /books/intake 入库并出报告")
    p_run.add_argument("--manifest", default="batch_manifest.json")
    p_run.add_argument("--dry-run", action="store_true", help="只校验并展示将提交的条目，不调 API")
    p_run.add_argument("--yes", action="store_true",
                       help="把 recognized 条目视为已确认直接入库（须最近 eval 书级准确率 ≥ 75%%）")
    p_run.add_argument("--force", action="store_true", help="越过 eval 门控强制 --yes（人工判断责任自负）")
    p_run.add_argument("--retry-failed", action="store_true", help="连同上次 failed 的条目重试")
    p_run.add_argument("--eval-dir", default=str(PROJECT_ROOT / "tests/eval"),
                       help="eval 结果目录（--yes 门控读取）")
    p_run.add_argument("--report", default="batch_report.json", help="报告输出路径")
    p_run.add_argument("--price", type=float, default=None, help="批量默认价格（条目自身值优先）")
    p_run.add_argument("--channel", default=None, help="批量默认购买渠道")
    p_run.add_argument("--location", default=None, help="批量默认存放位置")
    p_run.add_argument("--member-id", type=int, default=None, help="批量默认成员 ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "run":
        return cmd_run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
