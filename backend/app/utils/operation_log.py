from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models import OperationLog
from app.utils.book_helpers import serialize_json_dict

logger = logging.getLogger(__name__)


def log_operation(
    db: Session,
    *,
    action: str,
    member_id: int | None = None,
    channel: str | None = None,
    payload: dict | None = None,
    result: str | None = None,
    operator_member_id: int | None = None,
) -> None:
    """member_id=数据归属人；operator_member_id=实际操作者（权限阶段 2 代操作显式化，
    两者不同时 payload 附加 acting_for 标记）。"""
    if operator_member_id is not None and operator_member_id != member_id:
        payload = {**(payload or {}), "operator_member_id": operator_member_id, "acting_for": True}
    db.add(
        OperationLog(
            member_id=member_id,
            channel=channel,
            action=action,
            payload=serialize_json_dict(payload),
            result=result,
        )
    )


def log_and_commit(
    db: Session,
    *,
    action: str,
    member_id: int | None = None,
    channel: str | None = None,
    payload: dict | None = None,
    result: str | None = None,
    operator_member_id: int | None = None,
) -> None:
    """追加审计日志并提交。日志失败不应影响已成功的业务结果。"""
    try:
        log_operation(
            db,
            action=action,
            member_id=member_id,
            channel=channel,
            payload=payload,
            result=result,
            operator_member_id=operator_member_id,
        )
        db.commit()
    except Exception:
        logger.warning("operation_log 写入失败（action=%s）", action, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass