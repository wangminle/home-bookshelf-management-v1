"""共享安全审计服务（权限阶段 1 补齐，CHK-071 遗漏项 5）。

基线 §11.1/§14-阶段1 验收：REST、Public Catalog 与后续 MCP 共用同一审计
入口。存储复用 operation_logs 表（action 前缀 security.），不新建表、不迁移。

成本控制（防止匿名请求变成数据库写放大 / DoS 向量，基线 §11.3）：
- 拒绝事件（LAN_REQUIRED / ANONYMOUS_CATALOG_DISABLED / RATE_LIMITED 等）：
  按 (event, outcome, subject) 内存抑制聚合，默认每 60s 至多 1 条；
  调用方可用 suppress_key 细化维度（MCP deny 按 method/tool 分键）；
- 放行事件：更长抑制间隔（默认 600s）采样留痕，保留每主体首次访问证据；
  需要逐次审计的门控（MCP 真实数据读取）传 suppress_seconds=0 禁用采样；
- 审计写入失败仅记应用日志，绝不影响业务响应；抑制时间**只在写入成功后**
  登记（CHK-077/BUG-209）：写失败不进入抑制窗口，后续同键事件继续如实
  返回 failed，MCP 等真实数据门控的 fail-closed 不会被"失败后抑制"绕过；
  写失败 10s 内的同键事件不再重试（防日志放大），但仍返回 failed；
- 多实例部署时抑制表为进程内（与 rate_limit 同边界），见基线 §12.2。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from sqlalchemy import select

from app import db as db_module
from app.models import OperationLog

logger = logging.getLogger("security_audit")

DEFAULT_DENY_SUPPRESS_SECONDS = 60
DEFAULT_ALLOW_SUPPRESS_SECONDS = 600

_suppressed: dict[tuple[str, ...], float] = {}
_failed_recently: dict[tuple[str, ...], float] = {}
_lock = threading.Lock()
# 写失败后的同键退避窗口：期间不再重试写入（仍返回 failed），防日志放大
_FAILURE_RETRY_SECONDS = 10.0


def _now() -> float:
    return time.monotonic()


def reset() -> None:
    """清空抑制表与失败退避表（测试用）。"""
    with _lock:
        _suppressed.clear()
        _failed_recently.clear()


# 写入结果三态（CHK-073/BUG-198）：真实数据门控方对 "failed" 必须 fail-closed
AUDIT_WRITTEN = "written"
AUDIT_SUPPRESSED = "suppressed"
AUDIT_FAILED = "failed"


def log_security_event(
    *,
    event_type: str,
    outcome: str,
    subject: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
    suppress_seconds: int | None = None,
    session=None,
    suppress_key: tuple[str, ...] | None = None,
) -> str:
    """记录一条安全事件，返回 written / suppressed / failed。

    - suppressed：命中抑制窗口（按设计采样/聚合，属正常）；
    - failed：数据库写入失败（仅记应用日志）——MCP 等真实数据门控
      对该结果必须拒绝返回数据（fail-closed）；
    outcome: "deny" | "allow"；subject：匿名=客户端 IP，Agent=agent:<client_id>，
    Web=web:<member_id>。session 传入时复用调用方会话，否则自建短会话。
    - suppress_seconds=0：不采样、逐次写入（MCP allow 审计用）；
    - suppress_key：自定义抑制维度（MCP deny 按 method/tool 分键），默认
      (event_type, outcome, subject)；
    - BUG-209：抑制时间只在写入成功后登记。写失败不进抑制窗口，后续同键
      事件继续尝试并如实返回 failed，真实数据门控的 fail-closed 不被绕过。
    """
    if suppress_seconds is None:
        suppress_seconds = (
            DEFAULT_DENY_SUPPRESS_SECONDS if outcome == "deny" else DEFAULT_ALLOW_SUPPRESS_SECONDS
        )
    key = suppress_key if suppress_key is not None else (event_type, outcome, subject or ip or "-")
    now = _now()
    with _lock:
        last = _suppressed.get(key)
        if last is not None and now - last < suppress_seconds:
            return AUDIT_SUPPRESSED
        # BUG-209：写失败后的同键退避（防日志放大），期间不再重试但返回 failed
        failed_at = _failed_recently.get(key)
        if failed_at is not None and now - failed_at < _FAILURE_RETRY_SECONDS:
            return AUDIT_FAILED

    payload = {
        "event_type": event_type,
        "outcome": outcome,
        "subject": subject,
        "ip": ip,
        "details": details or {},
    }
    try:
        if session is not None:
            session.add(OperationLog(
                channel="security",
                action=f"security.{event_type}"[:50],
                payload=json.dumps(payload, ensure_ascii=False),
            ))
            session.commit()
        else:
            with db_module.SessionLocal() as db:
                db.add(OperationLog(
                    channel="security",
                    action=f"security.{event_type}"[:50],
                    payload=json.dumps(payload, ensure_ascii=False),
                ))
                db.commit()
        # BUG-209：抑制时间只在写入成功后登记（失败不进窗口）
        with _lock:
            _suppressed[key] = _now()
            _failed_recently.pop(key, None)
        return AUDIT_WRITTEN
    except Exception:  # noqa: BLE001 — 审计失败不阻断业务（是否 fail-closed 由调用方按门控决定）
        with _lock:
            _failed_recently[key] = _now()
        logger.exception("security audit write failed: %s", event_type)
        return AUDIT_FAILED


def list_security_events(session, *, event_type: str | None = None, limit: int = 100) -> list[OperationLog]:
    """查询安全事件（运维排查/测试用）。"""
    stmt = select(OperationLog).where(OperationLog.channel == "security")
    if event_type:
        stmt = stmt.where(OperationLog.action == f"security.{event_type}"[:50])
    stmt = stmt.order_by(OperationLog.id.desc()).limit(limit)
    return list(session.scalars(stmt))
