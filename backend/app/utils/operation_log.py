from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import OperationLog
from app.utils.book_helpers import serialize_json_dict


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
