"""批量入库清单（manifest）v1/v2 读写、校验与升级共享模块。

BI-01 契约实现。契约文档：design/plans/拍照入库契约基线v2-BI01-20261002.md

版本约束（冻结）：
- 读：支持 version 1 与 2。version 缺失按 1 处理；version 大于本模块支持的
  最大版本时 load_manifest 直接失败退出，且绝不写回——旧工具不能解释的
  高版本清单必须明确拒绝，避免把未知字段静默丢弃。
- 写：总是写 version 2。首次把磁盘上的 v1 清单升级为 v2 保存前，先把原
  v1 文件备份为 "<name>.v1.bak"（已存在则不覆盖，保留最早原件）。
- 迁移不丢弃历史 result：v1 条目的 result 字典原样保留，仅补充缺省键。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 写出的清单版本；SUPPORTED 为可读版本（含 v1 兼容读取）
MANIFEST_VERSION = 2
SUPPORTED_MANIFEST_VERSIONS = (1, 2)

# 状态机（v1 基础上新增 outcome_unknown，BI-04 使用）：
# pending → recognized →（用户核对）confirmed / skip →（run）imported / failed / outcome_unknown
# outcome_unknown：请求已发出但回执丢失（如读超时），结果未知。不得被
# --retry-failed 自动重试；须核对后经 reconcile 或显式 --retry-unknown 决定。
VALID_STATUS = ("pending", "recognized", "confirmed", "skip", "imported", "failed", "outcome_unknown")

# 图片角色（照片条目 v2）
VALID_ROLES = ("cover", "barcode", "other", "unknown")

# ── 错误码（run 报告 entries[].error_code 与 manifest result.error_code 共用）──
# 稳定错误码：CLI 与报告使用相同错误码（契约 §4.1）。消息可变，码不可变。
ERROR_CODE_MISSING_INPUT = "missing_input"                    # 预检：缺 isbn 且缺 title
ERROR_CODE_FILE_MISSING = "file_missing"                      # 预检：封面文件缺失
ERROR_CODE_INVALID_ISBN = "invalid_isbn"                      # 手工 ISBN 校验失败
ERROR_CODE_IMAGE_CORRUPTED = "image_corrupted"                # 图片损坏/无法读取
ERROR_CODE_BARCODE_UNAVAILABLE = "barcode_dependency_unavailable"  # 后端缺 pyzbar/zbar
ERROR_CODE_BARCODE_TIMEOUT = "barcode_decode_timeout"         # 条码解码超时
ERROR_CODE_ISBN_CONFLICT = "isbn_ownership_conflict"          # ISBN 归属冲突（阻断）
ERROR_CODE_BAD_REQUEST = "bad_request"                        # 其他 400
ERROR_CODE_CONFLICT = "conflict"                              # 409
ERROR_CODE_UNAUTHORIZED = "unauthorized"                      # 401
ERROR_CODE_FORBIDDEN = "forbidden"                            # 403
ERROR_CODE_SERVICE_UNAVAILABLE = "service_unavailable"        # 其他 503
ERROR_CODE_TIMEOUT = "timeout"                                # 连接 API 超时（读超时 → 结果未知）
ERROR_CODE_RECEIPT_LOST = "receipt_lost"                      # 连接建立后读写失败/协议错误/2xx 不可解析（→ 结果未知）
ERROR_CODE_NETWORK_ERROR = "network_error"                    # 连接失败/非 JSON（确定未提交，可安全重试）
ERROR_CODE_UNKNOWN = "unknown"

# ── 警告码（与后端 IntakeOut.warnings[].code 一致，契约 §4.1/4.2）──
WARNING_BARCODE_UNAVAILABLE = "barcode_dependency_unavailable"
WARNING_BARCODE_TIMEOUT = "barcode_decode_timeout"
WARNING_BARCODE_NOT_FOUND = "barcode_not_found"
WARNING_BARCODE_INVALID_CHECKSUM = "barcode_invalid_checksum"
WARNING_METADATA_MISSING = "metadata_missing"
WARNING_METADATA_FIELD_CONFLICT = "metadata_field_conflict"
WARNING_ISBN_CONFLICT = "isbn_ownership_conflict"

WARNING_CODES = (
    WARNING_BARCODE_UNAVAILABLE,
    WARNING_BARCODE_TIMEOUT,
    WARNING_BARCODE_NOT_FOUND,
    WARNING_BARCODE_INVALID_CHECKSUM,
    WARNING_METADATA_MISSING,
    WARNING_METADATA_FIELD_CONFLICT,
    WARNING_ISBN_CONFLICT,
)

# v2 条目相对 v1 新增字段的缺省值（按此顺序写出到 JSON，便于人工核对）
ENTRY_V2_DEFAULTS: dict[str, Any] = {
    "photo_id": None,
    "sha256": None,
    "role": "cover",
    "authors": None,  # 作者数组（agent 可填；缺省回落 singular author）
    "confirmed_fields": [],
    "warnings": [],
    "candidate_id": None,
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _compact_now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def new_run_id() -> str:
    """每次 run 的稳定标识，写入报告与每个条目 result。"""
    return f"run-{_compact_now()}-{secrets.token_hex(2)}"


def new_batch_id() -> str:
    return f"batch-{_compact_now()}-{secrets.token_hex(2)}"


def new_photo_id(existing_ids: list[str | None]) -> str:
    """为新增照片分配稳定 photo_id：p0001 递增，延续清单内已有最大序号。"""
    max_seq = 0
    for pid in existing_ids:
        if not pid:
            continue
        m = re.fullmatch(r"p(\d+)", pid)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return f"p{max_seq + 1:04d}"


def sha256_of_file(path: Path) -> str | None:
    """照片内容哈希。只证明照片相同，不能单凭哈希判断两张照片是同一本书。"""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _die(msg: str) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


# ── 错误分类 ──

def classify_client_error(message: str | None) -> str:
    """把 BookshelfClient 的错误消息映射为稳定错误码。

    消息格式见 cli/bookshelf/client.py：HTTP 错误带 "[HTTP nnn]" 前缀，
    连接失败为"无法连接 API：…"、超时为"连接 API 超时：…"。
    """
    msg = (message or "").strip()
    if not msg:
        return ERROR_CODE_UNKNOWN
    if msg.startswith("[HTTP "):
        code = msg[6:9]
        if code == "400":
            if "ISBN 归属冲突" in msg:
                return ERROR_CODE_ISBN_CONFLICT
            if "ISBN" in msg:
                return ERROR_CODE_INVALID_ISBN
            if "无法识别图片文件" in msg or "图片" in msg:
                return ERROR_CODE_IMAGE_CORRUPTED
            return ERROR_CODE_BAD_REQUEST
        if code == "401":
            return ERROR_CODE_UNAUTHORIZED
        if code == "403":
            return ERROR_CODE_FORBIDDEN
        if code == "409":
            return ERROR_CODE_CONFLICT
        if code == "503":
            if "pyzbar" in msg or "zbar" in msg:
                return ERROR_CODE_BARCODE_UNAVAILABLE
            return ERROR_CODE_SERVICE_UNAVAILABLE
        if code.startswith("5"):
            return ERROR_CODE_SERVICE_UNAVAILABLE
        return ERROR_CODE_UNKNOWN
    if msg.startswith("连接 API 超时"):
        if "未建立连接" in msg:
            return ERROR_CODE_NETWORK_ERROR
        return ERROR_CODE_TIMEOUT
    if msg.startswith("回执丢失（可能已提交）"):
        return ERROR_CODE_RECEIPT_LOST
    if msg.startswith("无法连接 API") or msg.startswith("API 返回非 JSON"):
        return ERROR_CODE_NETWORK_ERROR
    if "缺少 isbn 和 title" in msg:
        return ERROR_CODE_MISSING_INPUT
    if "封面文件缺失" in msg:
        return ERROR_CODE_FILE_MISSING
    return ERROR_CODE_UNKNOWN


# 是否为"结果未知"错误：请求可能已被服务端受理，只是回执丢失。
# M1 语义：结果未知不得自动重试，须核对后决定（契约 §4.5）。
# timeout 与 receipt_lost 均属此类；network_error（连接失败等）确定未提交，
# 可安全重试（BUG-261）。
def is_outcome_unknown_error(error_code: str) -> bool:
    return error_code in (ERROR_CODE_TIMEOUT, ERROR_CODE_RECEIPT_LOST)


# ── eval 门控证据核验（BI-10，规划 §4.7）──

# 试点样本下限：至少 20 本不同书（按分组计，不是照片数）
DEFAULT_MIN_VERIFIED_GROUPS = 20
DEFAULT_GATE_TASK_TYPE = "cover_title_author"


def verify_eval_evidence(
    result: dict | None,
    *,
    expected_task_type: str = DEFAULT_GATE_TASK_TYPE,
    expected_prompt_version: str | None = None,
    expected_model_fingerprint: str | None = None,
    expected_golden_sha: str | None = None,
    min_verified_groups: int = DEFAULT_MIN_VERIFIED_GROUPS,
) -> tuple[bool, str]:
    """核验 eval 结果是否可作为 --yes 自动确认的证据。

    增强门控要求匹配模型配置、提示词、测试集版本和任务类型（规划 §4.7）：
    换模型/提示词/任务类型后旧结果不通用；样本未核验、样本不足或证据不匹配
    时拒绝。expected_* 为 None 表示该维度不在本工具侧比对（批量脚本无后端
    模型配置访问权，由操作者对照设置页或最近报告核对），但结果文件必须
    **携带**完整身份字段，缺一不可。

    完成度（BUG-258）：报告必须区分计划/已完成/已完成且已核验样本；limit
    截断、中断恢复的**部分结果**与冒烟报告禁止作为自动确认依据——progress
    缺失或未完成一律拒绝。
    """
    if not isinstance(result, dict):
        return False, "eval 结果不可读"
    identity = result.get("identity")
    if not isinstance(identity, dict):
        return False, "eval 结果缺 identity（旧格式证据不含模型/提示词/测试集身份，不通用）"
    progress = result.get("progress")
    if not isinstance(progress, dict):
        return False, ("eval 结果缺 progress（计划/已完成/已核验样本数），"
                       "无法证明评测完整完成，禁止作为自动确认依据")
    planned = progress.get("planned_photos")
    completed = progress.get("completed_photos")
    if not isinstance(planned, int) or not isinstance(completed, int) \
            or planned <= 0 or completed < planned:
        return False, (f"eval 报告未完成（计划 {planned} 张，已完成 {completed} 张；"
                       f"limit 截断或中断恢复的部分结果/冒烟报告），禁止作为自动确认依据")
    if identity.get("task_type") != expected_task_type:
        return False, (f"eval 任务类型 {identity.get('task_type')!r} ≠ {expected_task_type!r}，"
                       f"证据不适用本用途")
    for key, expected, label in (
        ("prompt_version", expected_prompt_version, "提示词版本"),
        ("model_fingerprint", expected_model_fingerprint, "模型配置指纹"),
        ("golden_sha256", expected_golden_sha, "测试集版本"),
    ):
        actual = identity.get(key)
        if not actual or not isinstance(actual, str):
            return False, f"eval 结果缺 identity.{key}（无法绑定{label}）"
        if expected is not None and actual != expected:
            return False, (f"identity.{key}={actual} 与当前{label} {expected} 不符，"
                           f"旧证据不通用")
    # 已核验样本数取"已完成且已核验"口径（progress），不是全集口径
    # （sample.verified_groups 是金标准全集统计，limit 截断时会虚高，BUG-258/259）
    verified_groups = progress.get("completed_verified_groups")
    if not isinstance(verified_groups, int) or verified_groups < min_verified_groups:
        actual = verified_groups if isinstance(verified_groups, int) else "未知"
        return False, (f"实际完成且已人工核验样本 {actual} 本不同书，低于下限 "
                       f"{min_verified_groups}——样本未核验或不足，禁止自动确认")
    return True, "身份、完成度与样本核验通过"


# ── manifest 读写 ──

def _entry_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """把单个条目规范为 v2 形态：补缺省键，保留既有全部键与历史 result。"""
    entry = dict(raw)
    for key, default in ENTRY_V2_DEFAULTS.items():
        if entry.get(key) is None:
            entry[key] = default() if callable(default) else default
        # 深拷贝缺省可变值，避免多个条目共享同一 list
        if isinstance(entry[key], list):
            entry[key] = list(entry[key])
    if entry.get("role") not in VALID_ROLES:
        entry["role"] = "cover"
    if not isinstance(entry.get("confirmed_fields"), list):
        entry["confirmed_fields"] = []
    if not isinstance(entry.get("warnings"), list):
        entry["warnings"] = []
    return entry


def upgrade_manifest(data: dict[str, Any]) -> dict[str, Any]:
    """在内存中把清单规范为 v2 形态（version 字段保持读入值，写盘时置 2）。"""
    out = dict(data)
    version = out.get("version") or 1
    out["version"] = version if isinstance(version, int) else 1
    if out.get("batch_id") is None:
        out["batch_id"] = None  # 保存时若仍缺失则生成并固化
    entries = []
    photo_ids: list[str | None] = []
    for raw in out.get("entries", []):
        entry = _entry_v2(raw)
        if not entry.get("photo_id"):
            entry["photo_id"] = new_photo_id(photo_ids)
        photo_ids.append(entry["photo_id"])
        entries.append(entry)
    out["entries"] = entries
    return out


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        _die(f"清单不存在: {path}（先执行 scan 生成）")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(f"清单不是合法 JSON: {path}（{exc}）")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        _die(f"清单格式错误: {path}（应为含 entries 数组的对象）")
    version = manifest.get("version") or 1
    if not isinstance(version, int) or version not in SUPPORTED_MANIFEST_VERSIONS:
        # 契约：旧工具不能解释的高版本清单必须明确拒绝，且绝不写回
        _die(
            f"不支持的清单版本: {path}（version={version}，本工具支持 "
            f"{'/'.join(str(v) for v in SUPPORTED_MANIFEST_VERSIONS)}；已拒绝读取，不会写回）"
        )
    bad = [e.get("file", "?") for e in manifest["entries"] if e.get("status") not in VALID_STATUS]
    if bad:
        _die(f"清单含非法 status 的条目: {bad}（合法值 {VALID_STATUS}）")
    upgraded = upgrade_manifest(manifest)
    # 保留原始顶层键（不丢弃任何历史信息）
    return upgraded


def _on_disk_version(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, int) else 1


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """原子写出 v2 清单；磁盘上还是 v1 时先备份原件。

    逐条保存（BI-04）复用本函数：临时文件 + os.replace 原子替换，
    中断不会留下半写的清单。
    """
    version = manifest.get("version")
    if isinstance(version, int) and version > MANIFEST_VERSION:
        _die(f"拒绝写出不支持的清单版本 version={version}（本工具最高 {MANIFEST_VERSION}）")

    if path.exists() and _on_disk_version(path) == 1:
        backup = path.with_name(path.name + ".v1.bak")
        if not backup.exists():
            # 保留最早的原件，不覆盖已有备份
            backup.write_bytes(path.read_bytes())

    manifest["version"] = MANIFEST_VERSION
    if not manifest.get("batch_id"):
        manifest["batch_id"] = new_batch_id()
    manifest["updated_at"] = now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
