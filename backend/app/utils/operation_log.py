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
) -> None:
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
        )
        db.commit()
    except Exception:
        logger.warning("operation_log 写入失败（action=%s）", action, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass