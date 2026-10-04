"""金标准 v2 读写、校验与统计（BI-08，规划 §4.7）。

v2 在 v1 条目（id/file/task/difficulty/expected）上**增量**新增字段，顶层保持
JSON 数组——旧 compare 工具仍可读：

| 字段 | 语义 |
| --- | --- |
| group | 同一本书的照片分组 ID。同一本书的多张照片必须同组；切分校准/验证集按组分，避免重复照片泄漏 |
| verified | 人工逐项核对过该标注（真实金标准须人工核对；识别结果只能作标注草稿） |
| isbn_visible | 封面是否实际印有 ISBN/条码——区分"模型漏读 ISBN"与"封面本来没有" |
| authors_on_cover | 封面是否署作者——区分"封面未署作者"与"模型漏作者" |

约定：真实封面不进 Git；合成（sim_/vlm- 前缀）样例可分发。
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

GOLDEN_ENTRY_VERSION = 2
DEFAULT_TASK = "cover_title_author"


class GoldenError(ValueError):
    pass


def normalize_entry(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GoldenError(f"金标准条目 #{index} 不是对象")
    entry_id = raw.get("id")
    if not entry_id:
        raise GoldenError(f"金标准条目 #{index} 缺 id")
    if not raw.get("file"):
        raise GoldenError(f"条目 {entry_id} 缺 file")
    entry = dict(raw)
    entry.setdefault("task", DEFAULT_TASK)
    entry.setdefault("group", str(entry_id))  # v1 无分组 → 一图一组
    entry.setdefault("verified", False)
    entry.setdefault("isbn_visible", False)
    entry.setdefault("authors_on_cover", True)
    expected = entry.get("expected")
    if not isinstance(expected, dict) or not expected.get("title"):
        raise GoldenError(f"条目 {entry_id} 缺 expected.title（书名是必需锚点）")
    return entry


def load_golden(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenError(f"金标准无法读取: {path}（{exc}）") from exc
    if not isinstance(raw, list) or not raw:
        raise GoldenError(f"金标准应为非空 JSON 数组: {path}")
    entries = [normalize_entry(item, i) for i, item in enumerate(raw)]
    ids = [e["id"] for e in entries]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise GoldenError(f"金标准存在重复 id: {dupes}")
    return entries


def golden_sha256(entries: list[dict[str, Any]]) -> str:
    """标注内容指纹（不含 verified/图片）：门控绑定测试集版本用。"""
    material = json.dumps(
        [{"id": e["id"], "file": e["file"], "group": e["group"],
          "task": e.get("task"), "difficulty": e.get("difficulty"),
          "expected": e.get("expected")} for e in entries],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def golden_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, int] = {}
    for e in entries:
        groups[e["group"]] = groups.get(e["group"], 0) + 1
    verified_groups = {
        e["group"] for e in entries if e.get("verified") and e["group"]
    }
    by_difficulty: dict[str, int] = {}
    for e in entries:
        d = e.get("difficulty") or "normal"
        by_difficulty[d] = by_difficulty.get(d, 0) + 1
    return {
        "photos_total": len(entries),
        "distinct_groups": len(groups),
        "groups_with_multiple_photos": sum(1 for n in groups.values() if n > 1),
        "verified_photos": sum(1 for e in entries if e.get("verified")),
        "verified_groups": len(verified_groups),
        "isbn_visible_photos": sum(1 for e in entries if e.get("isbn_visible")),
        "authors_not_on_cover": sum(1 for e in entries if e.get("authors_on_cover") is False),
        "by_difficulty": by_difficulty,
    }


def split_by_group(
    entries: list[dict[str, Any]],
    *,
    validation_ratio: float = 0.3,
    seed: int | None = 20261002,
) -> tuple[list[str], list[str]]:
    """按书分组切分校准/验证集：同组照片永远落在同一侧，杜绝重复照片泄漏。"""
    if not 0 < validation_ratio < 1:
        raise GoldenError("validation_ratio 须在 (0,1)")
    groups: dict[str, list[str]] = {}
    for e in entries:
        groups.setdefault(e["group"], []).append(e["id"])
    names = sorted(groups)
    rng = random.Random(seed)
    rng.shuffle(names)
    cut = max(1, min(len(names) - 1, round(len(names) * validation_ratio))) if len(names) > 1 else 1
    validation_groups = set(names[:cut]) if len(names) > 1 else set(names)
    calibration_groups = set(names) - validation_groups
    calibration = [i for g in sorted(calibration_groups) for i in sorted(groups[g])]
    validation = [i for g in sorted(validation_groups) for i in sorted(groups[g])]
    return calibration, validation
