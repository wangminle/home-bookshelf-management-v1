"""LOC-18：把书目详情里的副本位置整理成 Agent 可直接转述的只读定位结论。

只做展示，不推断：
- 无 `locations:read` 时后端响应不含 placement/location_display/placement_version，
  这里报告 ``not_granted``，不猜测位置；
- 副本状态不是 ``in_shelf``（外借、遗失等）时只给出归属位置并明确“当前不在架”；
- 没有副本、没有位置时如实说明缺失，不补房间/书架/格子。
"""
from __future__ import annotations

from typing import Any

STATUS_LABELS = {
    "in_shelf": "在架",
    "lent_out": "外借",
    "lost": "遗失",
    "damaged": "损坏",
    "storage": "库存存放",
    "discarded": "已淘汰",
}


def _is_physical(copy: dict[str, Any]) -> bool:
    return (copy.get("copy_type") or "physical") == "physical"


def _access(copies: list[dict[str, Any]]) -> str:
    """locations:read 是否生效：看响应是否带 placement_version 键（未授权时整键缺失）。"""
    physical = [c for c in copies if _is_physical(c)]
    if not physical:
        return "unknown"
    return "granted" if all("placement_version" in c for c in physical) else "not_granted"


def _describe_copy(copy: dict[str, Any], access: str) -> dict[str, Any]:
    status = copy.get("status") or "in_shelf"
    label = STATUS_LABELS.get(status, status)
    legacy = (copy.get("location") or "").strip() or None
    out: dict[str, Any] = {
        "copy_id": copy.get("id"),
        "copy_type": copy.get("copy_type") or "physical",
        "status": status,
        "status_label": label,
        "placement": None,
        "location_display": None,
        "legacy_location": legacy if access == "granted" else None,
        "on_shelf": None,
    }
    if not _is_physical(copy):
        out["position_state"] = "not_applicable"
        out["note"] = "电子副本，无实体位置"
        return out

    out["on_shelf"] = status == "in_shelf"
    if access != "granted":
        out["position_state"] = "unknown"
        out["note"] = "当前身份没有 locations:read，无法读取结构化位置"
        return out

    placement = copy.get("placement")
    if placement:
        out["position_state"] = "structured"
        out["placement"] = placement
        out["location_display"] = copy.get("location_display")
        out["note"] = f"归属位置为{out['location_display']}" if out["location_display"] else "已结构化定位，但未返回位置文本"
    elif legacy:
        out["position_state"] = "legacy_text"
        out["note"] = f"仅有旧文字位置“{legacy}”，未登记结构化位置"
    else:
        out["position_state"] = "none"
        out["note"] = "未登记位置"
    return out


def _sentence(index: int, c: dict[str, Any]) -> str:
    if c["position_state"] == "not_applicable":
        return f"第 {index} 册为电子副本，无实体位置"
    head = f"第 {index} 册（副本 {c['copy_id']}）：{c['note']}"
    if c["on_shelf"] is False:
        return f"{head}，状态为{c['status_label']}，因此当前不在架"
    return f"{head}，状态为{c['status_label']}"


def summarize_locations(book: dict[str, Any]) -> dict[str, Any]:
    """book 为 GET /books/{id} 的 data。返回结构化结论与一句可转述的 summary。"""
    title = book.get("title") or "该书"
    copies_in = [c for c in (book.get("copies") or []) if isinstance(c, dict)]
    access = _access(copies_in)
    copies = [_describe_copy(c, access) for c in copies_in]

    if not copies:
        summary = f"《{title}》尚无副本登记，无法确定位置，不作推断。"
    else:
        physical_n = sum(1 for c in copies if c["copy_type"] == "physical")
        digital_n = len(copies) - physical_n
        parts = []
        idx = 0
        for c in copies:
            idx += 1
            parts.append(_sentence(idx, c))
        counts = f"登记有 {physical_n} 册实体副本"
        if digital_n:
            counts += f"、{digital_n} 个电子副本"
        if physical_n == 0:
            counts += "（没有实体副本，无书架位置）"
        summary = f"《{title}》{counts}。" + "；".join(parts) + "。"
        if access == "not_granted":
            summary += "（精确位置需 Owner 授予 locations:read。）"

    return {
        "book_id": book.get("id"),
        "title": book.get("title"),
        "location_access": access,
        "copies": copies,
        "summary": summary,
    }
