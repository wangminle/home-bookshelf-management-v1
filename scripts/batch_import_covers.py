#!/usr/bin/env python3
"""批量导入图书封面并建档。

已实施方案：design/achievements/批量导入图书封面并建档方案.md（路径① Agent + CLI）

流程：
    1. scan    扫描封面目录，生成/合并 batch_manifest.json 骨架（status=pending）
    2. Agent   用视觉逐张识别封面，填 title/author/isbn（status: pending → recognized）
    3. 用户    核对清单（status: recognized → confirmed / skip）
    4. run     只对 confirmed 条目调 POST /books/intake（复用 BookshelfClient.add，
               认证/超时/查重全部继承），出报告并回写 manifest（imported / failed）

安全阈值（方案 §4.4）：--yes 自动确认模式要求最近一次 eval 的门控口径书级完全
正确率（仅实际完成且已核验的不同书计算）≥ 75%，且证据身份（任务类型/提示词/
模型指纹/测试集指纹）须与本次实际使用逐一比对（四个 --gate-* 缺一不可），
否则拒绝执行（--force 可越过，仅限人工判断）。

用法：
    python3 scripts/batch_import_covers.py scan --dir ./covers
    python3 scripts/batch_import_covers.py status --manifest batch_manifest.json
    python3 scripts/batch_import_covers.py run --manifest batch_manifest.json --dry-run
    python3 scripts/batch_import_covers.py run --manifest batch_manifest.json
    python3 scripts/batch_import_covers.py reconcile --manifest batch_manifest.json

环境变量（同 CLI）：
    BOOKSHELF_API_URL   默认 http://127.0.0.1:8000
    BOOKSHELF_TOKEN     Agent Bearer Token（写接口必需；reconcile 只需读权限）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "cli"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from bookshelf.client import ApiOutcomeUnknownError, BookshelfClient  # noqa: E402

import batch_manifest as bm  # noqa: E402

# 清单契约（v1/v2 读写、错误码、photo_id、原子保存）集中在 batch_manifest.py
MANIFEST_VERSION = bm.MANIFEST_VERSION
VALID_STATUS = bm.VALID_STATUS
load_manifest = bm.load_manifest
save_manifest = bm.save_manifest
classify_client_error = bm.classify_client_error
new_run_id = bm.new_run_id
now_iso = bm.now_iso

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".tif", ".tiff"}

# 方案 §4.4：书级完全正确率低于该值时禁止 --yes 自动入库
BOOK_LEVEL_GATE = 0.75


def _die(msg: str) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


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
            photo_ids = [e.get("photo_id") for e in manifest["entries"]]
            manifest["entries"].append(
                {"photo_id": bm.new_photo_id(photo_ids), "file": rel, "sha256": None,
                 "role": "cover", "title": None, "author": None, "authors": None,
                 "isbn": None, "price": None, "channel": None, "location": None,
                 "member_id": None, "confirmed_fields": [], "status": "pending",
                 "note": None, "warnings": [], "candidate_id": None, "result": None}
            )
            added.append(rel)
    # 补齐/刷新内容哈希：v1 升级条目缺失时计算；已有但文件变化的不强行覆盖
    # （哈希由 scan 固化，run 不重算，避免大目录反复 IO）。
    for entry in manifest["entries"]:
        if entry.get("file") and not entry.get("sha256"):
            digest = bm.sha256_of_file(src / entry["file"])
            if digest:
                entry["sha256"] = digest
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


def check_auto_confirm_gate(
    eval_dir: Path,
    threshold: float,
    *,
    expected_task_type: str | None = None,
    expected_prompt_version: str | None = None,
    expected_model_fingerprint: str | None = None,
    expected_golden_sha: str | None = None,
) -> tuple[bool, str]:
    """--yes 模式前置检查（方案 §4.4 + BI-10 证据身份核验）。

    1. **身份逐一比对（BUG-257）**：必须与本次实际使用的模型配置指纹、提示词
       版本、任务类型、测试集版本逐一比对——四个期望身份缺一不可，缺失即拒绝，
       不允许默认 --yes 放行身份不匹配（或身份未绑定）的报告；
    2. 书级完全正确率取 **门控口径** metrics.book_level_accuracy_verified
       （仅由实际完成且人工核验的不同书计算，BUG-259），须达标；
    3. 证据必须携带 identity 与 progress（计划/已完成/已完成且已核验样本数，
       完成度不足即拒绝，BUG-258）。
    """
    missing = [label for label, value in (
        ("任务类型", expected_task_type), ("提示词版本", expected_prompt_version),
        ("模型配置指纹", expected_model_fingerprint), ("测试集版本", expected_golden_sha))
        if not value]
    if missing:
        return False, (f"--yes 自动确认必须与本次实际使用的{'/'.join(missing)}逐一比对："
                       f"请传入 --gate-task-type / --gate-prompt-version / "
                       f"--gate-model-fingerprint / --gate-golden-sha（人工越过用 --force）")
    result = latest_eval_result(eval_dir)
    if result is None:
        return False, f"未找到 eval 结果（{eval_dir}/results-*.json），先跑 scripts/run_cover_model_eval.py"
    metrics = result.get("metrics") or {}
    accuracy = metrics.get("book_level_accuracy_verified")
    if not isinstance(accuracy, (int, float)):
        return False, (f"eval 结果缺少 metrics.book_level_accuracy_verified"
                       f"（仅由实际完成且已核验样本计算的门控口径）: {result.get('generated_at')}")
    if accuracy < threshold:
        return False, (f"最近 eval 门控口径书级完全正确率 {accuracy:.1%} 低于 {threshold:.0%}"
                       f"（仅已核验样本；未核验样本不参与），须逐本人工确认"
                       f"（确认后置 confirmed 再 run）")
    ok, msg = bm.verify_eval_evidence(
        result,
        expected_task_type=expected_task_type,
        expected_prompt_version=expected_prompt_version,
        expected_model_fingerprint=expected_model_fingerprint,
        expected_golden_sha=expected_golden_sha,
    )
    if not ok:
        return False, msg
    identity = result.get("identity") or {}
    progress = result.get("progress") or {}
    return True, (f"最近 eval 门控口径书级完全正确率 {accuracy:.1%}"
                  f"（{result.get('generated_at')}；{identity.get('model_fingerprint')}"
                  f"/{identity.get('prompt_version')}；已完成 {progress.get('completed_photos')}"
                  f"/{progress.get('planned_photos')} 张，其中已核验 "
                  f"{progress.get('completed_verified_groups')} 本）")


# ── run ──

def _warn_barcode_capability(health_payload: Any) -> None:
    """BI-02 预检：复用受保护 /health 的 barcode_scan_available，不另造诊断入口。

    无 members:read 权限时（auth_protected）显示能力未知；最终判断由后端
    服务承担——入库接口对条码故障自行降级并返回警告。
    """
    data = health_payload.get("data") if isinstance(health_payload, dict) else None
    if not isinstance(data, dict):
        return
    available = data.get("barcode_scan_available")
    if available is False:
        print("⚠ 预检: 后端条码解码依赖不可用（缺 pyzbar/zbar）——"
              "图片条目若缺 isbn 且缺 title 将失败；有 title 的条目仍可降级入库")
    elif available is None and data.get("auth_protected"):
        print("⚠ 预检: 无 members:read 权限，条码能力未知；由后端最终判断")


def _classify_response(resp: Any, run_id: str) -> dict[str, Any]:
    """把 BookshelfClient.add() 的返回归类为 v2 result（契约 §4）。"""
    data = (resp or {}).get("data") if isinstance(resp, dict) else None
    data = data or {}
    book = data.get("book") or {}
    outcome = "exists" if data.get("already_exists") else "created"
    # BUG-264：警告全量保留（code/message/field/detail）——metadata_field_conflict
    # 的冲突字段名、确认值、外部值与来源必须可追溯到清单与历史报告
    warnings = [dict(w) for w in (data.get("warnings") or []) if isinstance(w, dict)]
    return {
        "outcome": outcome,
        "book_id": book.get("id"),
        "action": data.get("action"),
        "message": data.get("message"),
        "run_id": run_id,
        "at": now_iso(),
        "error_code": None,
        "error": None,
        "warnings": warnings,
    }


def _failure_result(run_id: str, error: str, *, outcome: str) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "book_id": None,
        "action": None,
        "message": None,
        "run_id": run_id,
        "at": now_iso(),
        "error_code": bm.classify_client_error(error),
        "error": str(error),
        "warnings": [],
    }


def cmd_run(args: argparse.Namespace, client: BookshelfClient | None = None) -> int:
    manifest_path = Path(args.manifest).expanduser()
    manifest = load_manifest(manifest_path)
    source_dir = Path(manifest.get("source_dir") or ".")
    entries = manifest["entries"]

    # BI-04（契约 §4/§6）：每次运行独立 run_id；报告默认带 run 时间戳，
    # 重跑不覆写唯一历史。结果未知条目不进自动重试（--retry-unknown 显式决定）。
    run_id = new_run_id()
    report_path = (Path(args.report).expanduser() if args.report
                   else Path(f"batch_report-{run_id[len('run-'):]}").with_suffix(".json"))

    statuses = [e["status"] for e in entries]
    target_status = {"confirmed"}
    if args.yes:
        target_status.add("recognized")
    if args.retry_failed:
        target_status.add("failed")
    if args.retry_unknown:
        target_status.add("outcome_unknown")
    todo = [e for e in entries if e["status"] in target_status]
    skipped = len(entries) - len(todo)

    if not todo:
        print(f"没有待入库条目（{statuses.count('confirmed')} confirmed / {statuses.count('recognized')} recognized）。")
        return 0

    if args.yes and not args.force:
        ok, msg = check_auto_confirm_gate(
            Path(args.eval_dir), BOOK_LEVEL_GATE,
            expected_task_type=getattr(args, "gate_task_type", None),
            expected_prompt_version=getattr(args, "gate_prompt_version", None),
            expected_model_fingerprint=getattr(args, "gate_model_fingerprint", None),
            expected_golden_sha=getattr(args, "gate_golden_sha", None),
        )
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

    # BUG-260：推断资格必须先写回清单，独立于接下来可能变为 failed/unknown
    # 的运行状态。包括预检失败：补回文件后重试仍保护原人工确认字段。
    inferred_confirmation = False
    for entry in todo:
        if entry.get("status") == "confirmed" and not entry.get("confirmed_fields"):
            confirmed = [f for f in ("title", "isbn") if entry.get(f)]
            if entry.get("authors") or entry.get("author"):
                confirmed.append("authors")
            entry["confirmed_fields"] = confirmed
            inferred_confirmation = True

    # 预检：缺识别键 / 缺文件的条目直接标 failed，不发请求
    preflight_failed: list[str] = []
    preflight_failed_ids: set[int] = set()
    for entry in todo:
        if not (entry.get("isbn") or entry.get("title")):
            entry["status"] = "failed"
            entry["result"] = _failure_result(run_id, "缺少 isbn 和 title，无法入库", outcome="failed")
        elif entry.get("file") and not (source_dir / entry["file"]).is_file():
            entry["status"] = "failed"
            entry["result"] = _failure_result(run_id, f"封面文件缺失: {entry['file']}", outcome="failed")
        else:
            continue
        preflight_failed.append(entry["file"] or "?")
        preflight_failed_ids.add(id(entry))
    todo = [e for e in todo if id(e) not in preflight_failed_ids]

    print(f"待入库 {len(todo)} 本（预检失败 {len(preflight_failed)}），跳过 {skipped} 条（非目标状态）"
          f"  run_id={run_id}")
    if preflight_failed:
        for f in preflight_failed:
            print(f"  ✗ 预检失败: {f}")

    if args.dry_run:
        print("\n[dry-run] 以下条目将被提交（不调 API）:")
        for i, entry in enumerate(todo, 1):
            label = entry.get("title") or entry.get("isbn") or "(无识别键)"
            img = entry.get("file") or "(无封面图)"
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
        health_payload = client.health()
    except RuntimeError as exc:
        _die(f"后端不可用: {exc}")
    _warn_barcode_capability(health_payload)

    # BI-04：预检失败也是执行结果，先落盘（dry-run 已在上文返回，不会走到这里）
    if preflight_failed or inferred_confirmation:
        save_manifest(manifest_path, manifest)

    results: list[dict[str, Any]] = []
    todo_ids = {id(e) for e in todo}
    for e in entries:
        if id(e) in todo_ids:
            continue  # 下面逐本处理
        if id(e) in preflight_failed_ids:
            # 仅本次预检失败计入 failed；历史遗留 failed 本次未触碰，应记 skipped（BUG-170）
            results.append({"file": e.get("file"), "title": e.get("title"), "author": e.get("author"),
                            "outcome": "failed", "book_id": None, "action": None,
                            "error_code": (e.get("result") or {}).get("error_code"),
                            "error": (e.get("result") or {}).get("error"),
                            "warnings": [], "run_id": run_id})
        else:
            results.append({"file": e.get("file"), "title": e.get("title"), "author": e.get("author"),
                            "outcome": "skipped", "book_id": None, "action": None,
                            "error_code": None, "error": None, "warnings": [], "run_id": run_id})
    summary = {"created": 0, "exists": 0, "failed": len(preflight_failed), "outcome_unknown": 0}
    started = time.monotonic()

    def _flush_report() -> None:
        """BI-04：报告随进度原子重写——中断后磁盘上保留最后一次完整快照。"""
        report = {
            "run_id": run_id,
            "manifest": str(manifest_path),
            "batch_id": manifest.get("batch_id"),
            "generated_at": now_iso(),
            "duration_seconds": round(time.monotonic() - started, 1),
            "summary": {
                "photos_total": len(entries),
                # BUG-248：total 与 entries 条数同口径（含 skipped）——
                # total == created+exists+failed+outcome_unknown+skipped。
                # failed 含预检失败（缺识别键/缺文件，请求未发到接口）；
                # 本轮实际提交数 = total-skipped-预检失败条数。
                "total": len(todo) + len(preflight_failed) + skipped,
                **summary,
                "skipped": skipped,
            },
            "entries": sorted(results, key=lambda r: (r["outcome"], r.get("file") or "")),
        }
        tmp = report_path.with_suffix(report_path.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, report_path)

    for i, entry in enumerate(todo, 1):
        label = entry.get("title") or entry.get("isbn") or entry.get("file")
        image = Path(source_dir / entry["file"]) if entry.get("file") else None
        # BUG-262：作者数组（authors）优先，回落 singular author（单元素数组，
        # 与后端 payload.author 等价）；客户端与后端 intake 均支持多值 authors，
        # 不得静默丢弃——实际下发的数组记录在回执与报告中，清单侧可追溯。
        entry_authors = entry.get("authors")
        authors = list(entry_authors) if isinstance(entry_authors, list) and entry_authors else None
        if authors is None and entry.get("author"):
            authors = [entry["author"]]
        # BI-03（契约 §5）+ BUG-260：确认资格以清单中的 confirmed_fields 持久
        # 保存，独立于运行状态——首次失败（failed/outcome_unknown）核对后重试
        # 仍继续传 prefer_confirmed，已确认字段不被外部元数据覆盖；--yes 的
        # recognized 条目未经人工核对，保持 default。确认字段推断覆盖作者数组。
        confirmed = [f for f in (entry.get("confirmed_fields") or [])]
        if not confirmed and entry.get("status") == "confirmed":
            confirmed = [
                field for field, key in (("title", "title"), ("isbn", "isbn"))
                if entry.get(key)
            ]
            if authors:
                confirmed.append("authors")
        policy_kwargs: dict = {}
        if confirmed or entry.get("status") == "confirmed":
            policy_kwargs = {"field_policy": "prefer_confirmed", "confirmed_fields": confirmed}
        try:
            resp = client.add(
                isbn=entry.get("isbn") or None,
                title=entry.get("title") or None,
                author=entry.get("author") or None,
                authors=authors,
                image=image,
                price=effective(entry, "price"),
                channel=effective(entry, "channel") or None,
                location=effective(entry, "location") or None,
                member_id=effective(entry, "member_id"),
                **policy_kwargs,
            )
            info = _classify_response(resp, run_id)
            info["authors"] = authors
            entry["status"] = "imported"
            summary[info["outcome"]] += 1
            mark = "✔ 已存在" if info["outcome"] == "exists" else "✔ 已入库"
            print(f"  [{i}/{len(todo)}] {mark}: {label} → ID {info['book_id']}")
            if info["warnings"]:
                for w in info["warnings"]:
                    print(f"      ⚠ [{w.get('code')}] {w.get('message')}")
        except RuntimeError as exc:
            error_code = bm.classify_client_error(str(exc))
            if isinstance(exc, ApiOutcomeUnknownError) or bm.is_outcome_unknown_error(error_code):
                # 契约 §7 / BUG-261：请求可能已被受理，仅回执丢失（读超时、
                # 连接建立后读写失败、2xx 响应不可解析等）。不并入 failed；
                # 自动重试暂停，核对后经 --retry-unknown 或 reconcile 决定。
                entry["status"] = "outcome_unknown"
                info = _failure_result(run_id, str(exc), outcome="outcome_unknown")
                summary["outcome_unknown"] += 1
                print(f"  [{i}/{len(todo)}] ⚠ 结果未知（自动重试暂停，核对后决定）: {label} — {exc}")
            else:
                # 连接未建立或明确业务拒绝：可重试；不确定的 5xx 由客户端
                # ApiOutcomeUnknownError 分流到上面的待核对路径。
                entry["status"] = "failed"
                info = _failure_result(run_id, str(exc), outcome="failed")
                summary["failed"] += 1
                print(f"  [{i}/{len(todo)}] ✗ 失败: {label} — {exc}")
        entry["result"] = info
        results.append({
            "file": entry.get("file"), "title": entry.get("title"), "author": entry.get("author"),
            "authors": authors,
            "outcome": info["outcome"], "book_id": info.get("book_id"),
            "action": info.get("action"), "error_code": info.get("error_code"),
            "error": info.get("error"), "warnings": info.get("warnings") or [],
            "run_id": run_id,
        })
        # BI-04：逐条落盘（清单原子替换 + 报告快照）。处理到第 N 条中断时，
        # 前 N-1 条的回执已在磁盘上，重跑从未处理条目继续。
        save_manifest(manifest_path, manifest)
        _flush_report()

    # 预检清空 todo 等场景下循环体不执行；最终再落一次报告快照（幂等）
    _flush_report()

    print(f"\n完成: 入库 {summary['created']}，已存在 {summary['exists']}，失败 {summary['failed']}，"
          f"结果未知 {summary['outcome_unknown']}，跳过 {skipped}")
    if summary["outcome_unknown"]:
        print("⚠ 存在结果未知条目：请先用 reconcile 或书目查询核对是否已入库，再决定 --retry-unknown。")
    print(f"报告: {report_path}")
    print(f"清单已回写: {manifest_path}")
    return 1 if (summary["failed"] or summary["outcome_unknown"]) else 0


# ── reconcile（BI-05：默认只读核对；--apply 仅改清单/报告，不改数据库）──

def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit() or ch in "Xx")


def _canonical_isbn13(value: Any) -> str | None:
    """去分隔符并统一为 ISBN-13（与 scripts/eval_cover_recognition.py 的
    normalize_isbn 同口径；BUG-278：ISBN-10 与 ISBN-13 直接比字符串会把同一本
    书（如 702000220X = 9787020002207）误报为不符，故两侧都换算后比较）。
    无法换算的脏输入原样返回（交给比对记不符），不让 reconcile 崩溃。"""
    digits = _digits(value).upper()
    if len(digits) == 10 and digits[:9].isdigit() and digits[9] in "0123456789X":
        core = "978" + digits[:9]
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
        return core + str((10 - total % 10) % 10)
    return digits or None


def _parse_mapping(spec: str) -> dict[str, Any]:
    import re as _re
    m = _re.fullmatch(r"(\d+)=(\d+)(?::([0-9Xx\-]+))?", spec.strip())
    if not m:
        _die(f"无效映射: {spec}（格式 from=to[:expected_isbn]，如 10=18:9787553820217）")
    return {"from": int(m.group(1)), "to": int(m.group(2)),
            "isbn13": _canonical_isbn13(m.group(3))}


def cmd_reconcile(args: argparse.Namespace, client: BookshelfClient | None = None) -> int:
    """最终关联核对（契约 §6 分口径汇总）。

    默认只读：逐条验证 book_id 是否存在、ISBN 是否冲突、封面引用是否存在；
    --apply 时仅修改清单/报告（悬空 ID 按显式映射重定向，先核对目标 ISBN），
    绝不写生产数据库。其他批次不得按书名自动猜测替代 ID。
    """
    manifest_path = Path(args.manifest).expanduser()
    manifest = load_manifest(manifest_path)
    entries = manifest["entries"]

    mappings: dict[int, dict[str, Any]] = {}
    for spec in args.map or []:
        mp = _parse_mapping(spec)
        mappings[mp["from"]] = mp

    if client is None:
        client = BookshelfClient()

    books: dict[int, dict[str, Any]] = {}

    def check(book_id: int) -> dict[str, Any]:
        if book_id in books:
            return books[book_id]
        try:
            resp = client.show(book_id)
            data = (resp or {}).get("data") or {} if isinstance(resp, dict) else {}
            books[book_id] = {
                "alive": True,
                "isbn13": _canonical_isbn13(data.get("isbn13")),
                "title": data.get("title"),
                "cover_path": data.get("cover_path"),
                "error": None,
            }
        except RuntimeError as exc:
            msg = str(exc)
            if msg.startswith("[HTTP 404]"):
                books[book_id] = {"alive": False, "error": msg}
            else:
                # 网络/权限等故障：无法判定存活，进"未知"，不当作已删除
                books[book_id] = {"alive": None, "error": msg}
        return books[book_id]

    # 显式映射：先核对目标存在且 ISBN 相符，才允许重定向（有依据的修正）。
    # BUG-263：原始创建回执（result.book_id）不可变；reconcile 后的最终关联
    # 单独记为 result.final_book_id（另有 reconciliations 追溯记录）。
    reconciliations: list[dict[str, Any]] = list(manifest.get("reconciliations") or [])
    remapped_entries = 0
    proposed_only = 0
    remap_notes: dict[int, str] = {}
    for from_id, mp in mappings.items():
        target = check(mp["to"])
        affected_all = [e for e in entries
                        if isinstance((e.get("result") or {}).get("book_id"), int)
                        and e["result"]["book_id"] == from_id]
        # 幂等：已重定向过的条目（final_book_id 已记录）不再重复处理
        affected = [e for e in affected_all
                    if (e.get("result") or {}).get("final_book_id") is None]
        already = len(affected_all) - len(affected)
        if not affected:
            if already:
                print(f"映射 {from_id}={mp['to']}: {already} 条已重定向过，跳过")
            else:
                print(f"⚠ 映射 {from_id}={mp['to']}：清单中没有条目引用 {from_id}，忽略")
            continue
        if target.get("alive") is not True:
            reason = f"目标 {mp['to']} 不存在或状态未知（{target.get('error') or '非存活'}）"
        elif mp["isbn13"] and _canonical_isbn13(target.get("isbn13")) != mp["isbn13"]:
            reason = (f"目标 {mp['to']} ISBN 为 {target.get('isbn13')}，"
                      f"与核对值 {mp['isbn13']} 不符，拒绝重定向")
        else:
            reason = ""
        if reason:
            remap_notes[from_id] = reason
            proposed_only += len(affected)
            print(f"⚠ 映射 {from_id}={mp['to']} 未应用: {reason}（条目保持待核对）")
            continue
        for e in affected:
            result = e["result"]
            if args.apply:
                # 回执不可变：result["book_id"] 保持原始创建回执（from_id），
                # 最终去向记入 final_book_id；目标书不因此计入净新增，
                # 原创建是否仍存活由独立核对决定（见下）。
                result["final_book_id"] = mp["to"]
                reconciliations.append({
                    "photo_id": e.get("photo_id"),
                    "file": e.get("file"),
                    "from_book_id": from_id,
                    "to_book_id": mp["to"],
                    "verified_isbn13": target.get("isbn13"),
                    "reason": "explicit-map+isbn-verified",
                    "at": now_iso(),
                    "operator": "reconcile",
                })
            remapped_entries += 1
        action = "已重定向" if args.apply else "将重定向（演练）"
        print(f"映射 {from_id}={mp['to']}: {len(affected)} 条 {action}"
              + (f"（ISBN 核对通过: {target.get('isbn13')}）" if target.get("isbn13") else ""))

    # 分口径汇总（契约 §6）。最终关联 = reconcile 后的去向（final_book_id），
    # 原始回执（book_id）保持不可变仅供追溯。
    rows: list[dict[str, Any]] = []
    photos_linked = 0
    linked_ids: set[int] = set()
    missing_book = unknown_book = isbn_mismatch = 0
    outcome_unknown_unlinked = 0
    for e in entries:
        result = e.get("result") or {}
        book_id = result.get("book_id")
        final_id = result.get("final_book_id")
        effective_id = final_id if isinstance(final_id, int) else book_id
        row = {
            "photo_id": e.get("photo_id"), "file": e.get("file"), "status": e.get("status"),
            "outcome": result.get("outcome"), "book_id": book_id,
            "final_book_id": final_id if isinstance(final_id, int) else None,
            "run_id": result.get("run_id"),
        }
        if isinstance(effective_id, int):
            info = check(effective_id)
            row["book_alive"] = info["alive"]
            if info["alive"] is True:
                photos_linked += 1
                linked_ids.add(effective_id)
                # BUG-278：两侧统一换算 ISBN-13 再比（ISBN-10 清单 vs ISBN-13
                # 后端为同一书，不得误报不符）
                entry_isbn = _canonical_isbn13(e.get("isbn"))
                live_isbn = _canonical_isbn13(info.get("isbn13"))
                if entry_isbn and live_isbn and entry_isbn != live_isbn:
                    row["isbn_match"] = False
                    isbn_mismatch += 1
                else:
                    row["isbn_match"] = True if (entry_isbn and live_isbn) else None
                if e.get("role") == "cover" and not info.get("cover_path"):
                    row["cover_missing"] = True
            elif info["alive"] is False:
                missing_book += 1
                row["book_alive"] = False
                dangling = final_id if isinstance(final_id, int) else book_id
                if dangling in remap_notes:
                    row["pending_review"] = remap_notes[dangling]
            else:
                unknown_book += 1
        elif result.get("outcome") == "outcome_unknown":
            # BUG-277：结果未知回执通常没有 book_id（请求可能已被受理），
            # 无从核对关联——计入未解决（unknown 口径），保持待核对并提醒，
            # 不得按"全部已关联"静默返回 0。其他无 book_id 的条目
            # （pending/skip 等未处理）不算未解决关联。
            outcome_unknown_unlinked += 1
            unknown_book += 1
            row["pending_review"] = ("结果未知且无 book_id：请求可能已入库，"
                                     "请先核对书目查询再决定 --retry-unknown")
        rows.append(row)

    # BUG-263 净新增口径：仅统计"可证明由本批创建且仍存活"的对象——
    # 原始回执 outcome=created 且当前存活。照片重映射不代表原创建对象
    # 被删除；原始创建与最终关联分开核对，既有映射目标本身不计新增。
    net_created_ids: set[int] = set()
    for e in entries:
        result = e.get("result") or {}
        book_id = result.get("book_id")
        if result.get("outcome") == "created" and isinstance(book_id, int) \
                and check(book_id).get("alive") is True:
            net_created_ids.add(book_id)

    summary = {
        "photos_total": len(entries),
        "photos_linked": photos_linked,
        "distinct_book_ids": len(linked_ids),
        "unresolved": {"missing_book": missing_book, "unknown": unknown_book,
                       "isbn_mismatch": isbn_mismatch},
        # 批次净新增：本批创建且最终仍存活的去重图书数；命中已有书不算新增
        "net_created": len(net_created_ids),
        # 清理/修正：重映射条数与被拒绝的悬空映射条数，不归为新入库成功
        "corrections": {"remapped": remapped_entries if args.apply else 0,
                        "remapped_proposed": 0 if args.apply else remapped_entries,
                        "remaps_rejected": proposed_only},
    }

    report = {
        "generated_at": now_iso(),
        "manifest": str(manifest_path),
        "batch_id": manifest.get("batch_id"),
        "applied": bool(args.apply),
        "books": {str(bid): info for bid, info in sorted(books.items())},
        "entries": rows,
        "summary": summary,
    }
    out_path = (Path(args.out).expanduser() if args.out
                else Path(f"batch_reconcile-{bm._compact_now()}.json"))
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out_path)

    if args.apply and remapped_entries:
        manifest["reconciliations"] = reconciliations
        save_manifest(manifest_path, manifest)

    print(f"\n核对: 照片 {summary['photos_total']}，已关联 {summary['photos_linked']}"
          f"（{summary['distinct_book_ids']} 本不同书），净新增 {summary['net_created']}")
    unresolved = summary["unresolved"]
    if any(unresolved.values()):
        print(f"未解决: 悬空 ID {unresolved['missing_book']}，状态未知 {unresolved['unknown']}，"
              f"ISBN 不符 {unresolved['isbn_mismatch']}（保持待核对，不自动改关联）")
        if outcome_unknown_unlinked:
            print(f"⚠ 其中 {outcome_unknown_unlinked} 条结果未知且无 book_id："
                  f"请求可能已入库，须先核对书目查询再决定 --retry-unknown。")
    print(f"{'已应用' if args.apply else '演练'}报告: {out_path}")
    if not args.apply and remapped_entries:
        print("演练模式未修改清单；加 --apply 应用重定向（仍不写数据库）。")
    return 1 if any(unresolved.values()) else 0


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
                       help="把 recognized 条目视为已确认直接入库（须最近 eval 门控口径书级准确率 ≥ 75%%，"
                            "且 --gate-task-type/--gate-prompt-version/--gate-model-fingerprint/"
                            "--gate-golden-sha 与本次实际配置逐一比对）")
    p_run.add_argument("--force", action="store_true", help="越过 eval 门控强制 --yes（人工判断责任自负）")
    p_run.add_argument("--retry-failed", action="store_true", help="连同上次 failed 的条目重试（不含 outcome_unknown）")
    p_run.add_argument("--retry-unknown", action="store_true",
                       help="连同上次 outcome_unknown 的条目重发（须先人工核对，请求可能已入库）")
    p_run.add_argument("--eval-dir", default=str(PROJECT_ROOT / "tests/eval"),
                       help="eval 结果目录（--yes 门控读取）")
    p_run.add_argument("--gate-task-type", default=None,
                       help="BUG-257：核验 eval 证据的任务类型（必须与本次评测实际任务一致，缺失即拒绝 --yes）")
    p_run.add_argument("--gate-prompt-version", default=None,
                       help="BI-10/BUG-257：核验 eval 证据的提示词版本（与后端 vision PROMPT_VERSION 一致时传入；缺失即拒绝 --yes）")
    p_run.add_argument("--gate-model-fingerprint", default=None,
                       help="BI-10/BUG-257：核验 eval 证据的模型配置指纹（模型重配后传入新指纹，防旧证据误放行；缺失即拒绝 --yes）")
    p_run.add_argument("--gate-golden-sha", default=None,
                       help="BUG-257：核验 eval 证据的测试集版本指纹（scripts/run_cover_model_eval.py 报告 identity.golden_sha256；缺失即拒绝 --yes）")
    p_run.add_argument("--report", default=None,
                       help="报告输出路径（默认 batch_report-<run时间戳>.json，不覆写历史报告）")
    p_run.add_argument("--price", type=float, default=None, help="批量默认价格（条目自身值优先）")
    p_run.add_argument("--channel", default=None, help="批量默认购买渠道")
    p_run.add_argument("--location", default=None, help="批量默认存放位置")
    p_run.add_argument("--member-id", type=int, default=None, help="批量默认成员 ID")

    p_rec = sub.add_parser("reconcile", help="最终关联核对（默认只读；--apply 仅改清单，不写数据库）")
    p_rec.add_argument("--manifest", default="batch_manifest.json")
    p_rec.add_argument("--apply", action="store_true",
                       help="应用已核对的重定向映射（修改清单/报告，不修改生产数据库）")
    p_rec.add_argument("--map", action="append", default=[], metavar="FROM=TO[:ISBN]",
                       help="悬空 ID 重定向映射，可重复。先核对目标 ISBN 再应用，"
                            "如 --map 10=18:9787553820217")
    p_rec.add_argument("--out", default=None,
                       help="核对报告输出路径（默认 batch_reconcile-<时间戳>.json）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "reconcile":
        return cmd_reconcile(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
