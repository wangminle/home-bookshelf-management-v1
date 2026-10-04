#!/usr/bin/env python3
"""元数据链诊断工具（RES-006 / BI-01）。

目的：用一组代表性中文书名/ISBN 分别诊断 NLC、Google Books、OpenLibrary，
区分「无结果 / 网络故障 / 限流 / 超时 / 错误元数据（英文错题）」，并保留
provider 级与链级耗时。结论回答两个问题：

1. source=manual 的条目里，哪些其实是外部请求故障（网络/限流/总时限）
   而非真的无结果——这两类此前被 http.py 吞异常后无法区分。
2. 各 provider 对中文书目的覆盖与返回质量（是否返回与查询完全无关的
   外文书目，即「英文错题」）。

用法（需要真实网络；API Key 经环境/后端配置读取，不写入报告）：
    backend/.venv/bin/python scripts/diagnose_metadata_chain.py \
        --out design/checkpoints/RES-006-metadata-diagnosis.json
    backend/.venv/bin/python scripts/diagnose_metadata_chain.py --probes my_probes.json

探针文件格式（JSON 数组，--probes）：
    [{"label": "翦商", "kind": "isbn", "value": "9787553820213", "expect_title": "翦商"},
     {"label": "小学问", "kind": "title", "value": "小学问", "author": null}]

报告结构：{generated_at, chain_deadline_sec, probes[], summary}。
诊断只读外部服务，不访问图书数据库、不写入任何书目。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PROVIDER_NAMES = ("nlc", "google_books", "openlibrary")

# 结局分类（稳定口径，勿改语义；报告按此聚合）
OUTCOME_OK = "ok"
OUTCOME_NO_RESULT = "no_result"
OUTCOME_NETWORK_ERROR = "network_error"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_SUSPECT_METADATA = "suspect_metadata"
OUTCOME_PROVIDER_ERROR = "provider_error"

# 代表性探针：以 20260930 批次涉及书目为主（翦商/小学问/节气手帖），
# 加两本常见中文书作对照。可用 --probes 扩充或替换。
DEFAULT_PROBES: list[dict[str, Any]] = [
    {"label": "三体·ISBN", "kind": "isbn", "value": "9787536692930", "expect_title": "三体"},
    {"label": "活着·ISBN", "kind": "isbn", "value": "9787506365437", "expect_title": "活着"},
    {"label": "翦商·书名", "kind": "title", "value": "翦商", "author": "李硕", "expect_title": "翦商"},
    {"label": "小学问·书名", "kind": "title", "value": "小学问", "expect_title": "小学问"},
    {"label": "节气手帖·书名", "kind": "title", "value": "节气手帖", "expect_title": "节气手帖"},
]


def _has_cjk(text: str | None) -> bool:
    return bool(text) and any("\u4e00" <= ch <= "\u9fff" for ch in text)


def is_suspect_metadata(probe: dict[str, Any], meta_title: str | None) -> bool:
    """错误元数据启发式：期望中文书名却返回无中文字符的书名，或字符集完全无交集。

    只标记「可疑」供人工核对，不据此断言 provider 错误。
    """
    expect = probe.get("expect_title")
    if not expect or not meta_title:
        return False
    if _has_cjk(expect) and not _has_cjk(meta_title):
        return True
    return not (set(expect.lower()) & set(meta_title.lower()))


def classify_provider_outcome(
    meta: Any,
    events: list[dict[str, Any]],
    error: str | None,
    probe: dict[str, Any],
) -> str:
    """把单次 provider 探测归类为稳定结局码。"""
    if error is not None:
        return OUTCOME_PROVIDER_ERROR
    if meta is not None:
        return (
            OUTCOME_SUSPECT_METADATA
            if is_suspect_metadata(probe, getattr(meta, "title", None))
            else OUTCOME_OK
        )
    # meta 为 None：按底层请求事件区分 无结果 / 网络 / 超时 / 限流
    if any(e.get("status") == 429 for e in events):
        return OUTCOME_RATE_LIMITED
    error_events = [e for e in events if e.get("error")]
    if any("Timeout" in (e.get("error") or "") or "timed out" in (e.get("error") or "")
           for e in error_events):
        return OUTCOME_TIMEOUT
    if error_events:
        return OUTCOME_NETWORK_ERROR
    return OUTCOME_NO_RESULT


def probe_provider(
    name: str,
    provider: Any,
    probe: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """对单个 provider 执行单探针的 fetch_by_isbn / search，计时并分类。"""
    events.clear()
    method_name = "fetch_by_isbn" if probe["kind"] == "isbn" else "search"
    args: tuple = (probe["value"],) if probe["kind"] == "isbn" else (probe["value"], probe.get("author"))
    started = time.monotonic()
    error: str | None = None
    meta = None
    try:
        meta = getattr(provider, method_name)(*args)
    except Exception as exc:  # provider 自身解析/构造异常：如实记录，不吞
        error = f"{exc.__class__.__name__}: {exc}"
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    outcome = classify_provider_outcome(meta, list(events), error, probe)
    row: dict[str, Any] = {
        "outcome": outcome,
        "elapsed_ms": elapsed_ms,
        "title_returned": getattr(meta, "title", None),
        "source": getattr(meta, "source", None),
        "error": error,
        "requests": [dict(e) for e in events],
    }
    return row


def run_diagnosis(
    probes: list[dict[str, Any]],
    *,
    providers: dict[str, Any],
    chain_fn: Callable[..., Any],
    add_listener: Callable[[Callable[[dict], None]], None],
    clear_listeners: Callable[[], None],
    chain_deadline_sec: float = 12.0,
) -> dict[str, Any]:
    """执行全部探针。依赖全部注入，便于测试用假 provider 复现口径。

    chain_fn 形如 metadata.chain.fetch_metadata(isbn=…, title=…, author=…)。
    """
    events: list[dict[str, Any]] = []
    collector = events.append
    report_probes: list[dict[str, Any]] = []

    for probe in probes:
        row: dict[str, Any] = {"label": probe.get("label") or probe.get("value"),
                               "kind": probe["kind"], "value": probe["value"],
                               "author": probe.get("author"),
                               "expect_title": probe.get("expect_title"),
                               "providers": {}, "chain": {}}
        for name in PROVIDER_NAMES:
            provider = providers.get(name)
            if provider is None:
                row["providers"][name] = {"outcome": OUTCOME_PROVIDER_ERROR,
                                          "error": "provider 未配置"}
                continue
            clear_listeners()
            add_listener(collector)
            try:
                row["providers"][name] = probe_provider(name, provider, probe, events)
            finally:
                clear_listeners()

        # 链级：完整 fetch_metadata 流程（primary→auxiliary→search，受 12s 总限）
        chain_kwargs = ({"isbn": probe["value"]} if probe["kind"] == "isbn"
                        else {"title": probe["value"], "author": probe.get("author")})
        clear_listeners()
        add_listener(collector)
        started = time.monotonic()
        try:
            meta = chain_fn(**chain_kwargs)
            chain_error = None
        except Exception as exc:
            meta, chain_error = None, f"{exc.__class__.__name__}: {exc}"
        finally:
            clear_listeners()
        chain_elapsed = round((time.monotonic() - started) * 1000, 1)
        row["chain"] = {
            "elapsed_ms": chain_elapsed,
            "deadline_sec": chain_deadline_sec,
            "deadline_hit": chain_elapsed >= chain_deadline_sec * 1000,
            "outcome": ("ok" if meta is not None
                        else OUTCOME_PROVIDER_ERROR if chain_error
                        else OUTCOME_NO_RESULT),
            "source": getattr(meta, "source", None),
            "title_returned": getattr(meta, "title", None),
            "error": chain_error,
        }
        report_probes.append(row)

    summary: dict[str, dict[str, int]] = {}
    for row in report_probes:
        for name, prov in row["providers"].items():
            summary.setdefault(name, {})
            summary[name][prov["outcome"]] = summary[name].get(prov["outcome"], 0) + 1

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "chain_deadline_sec": chain_deadline_sec,
        "probes": report_probes,
        "summary": {"by_provider_outcome": summary,
                    "total_probes": len(report_probes)},
    }


def load_backend_deps() -> dict[str, Any]:
    """懒加载后端元数据链。脚本入口才 import，测试注入假对象时零后端依赖。"""
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.services.metadata import chain as metadata_chain
    from app.services.metadata import http as metadata_http

    return {
        "providers": metadata_chain._build_providers(),
        "chain_fn": metadata_chain.fetch_metadata,
        "add_listener": metadata_http.add_response_listener,
        "clear_listeners": metadata_http.clear_response_listeners,
        "chain_deadline_sec": metadata_chain.METADATA_CHAIN_DEADLINE_SEC,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="元数据链来源/耗时诊断（RES-006；只读外部服务，不写图书库）")
    parser.add_argument("--out", default="metadata_diagnosis.json", help="诊断报告输出路径")
    parser.add_argument("--probes", default=None, help="自定义探针 JSON（默认内置代表性中文书目）")
    parser.add_argument("--quiet", action="store_true", help="只写报告，不打印逐项过程")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    probes = DEFAULT_PROBES
    if args.probes:
        try:
            probes = json.loads(Path(args.probes).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"错误: 探针文件无法读取: {exc}", file=sys.stderr)
            return 1
    if not isinstance(probes, list) or not probes:
        print("错误: 探针须为非空 JSON 数组", file=sys.stderr)
        return 1

    deps = load_backend_deps()
    report = run_diagnosis(probes, **deps)

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        for row in report["probes"]:
            print(f"\n== {row['label']}（{row['kind']}: {row['value']}）")
            for name, prov in row["providers"].items():
                line = f"  {name:13} {prov['outcome']:18} {prov['elapsed_ms']:>8.1f}ms"
                if prov.get("title_returned"):
                    line += f"  → {prov['title_returned'][:40]}"
                if prov.get("error"):
                    line += f"  [{prov['error'][:60]}]"
                print(line)
            chain = row["chain"]
            print(f"  {'链级(fetch_metadata)':13} {chain['outcome']:18} {chain['elapsed_ms']:>8.1f}ms"
                  + ("（触达总时限）" if chain["deadline_hit"] else ""))
        print(f"\n汇总: {json.dumps(report['summary']['by_provider_outcome'], ensure_ascii=False)}")
    print(f"报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
