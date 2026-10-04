"""读写后台多模态模型配置。API Key 不进入响应和操作日志。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.llm_settings import LlmSettings
from app.schemas.llm_settings import LlmSettingsOut, LlmSettingsUpdate

_SINGLETON_ID = 1


def empty_public() -> LlmSettingsOut:
    return LlmSettingsOut(
        enabled=False,
        display_name="识书模型",
        base_url="",
        model_id="",
        api_key_configured=False,
        api_key_hint=None,
        timeout_seconds=60,
        max_tokens=1024,
        temperature=0.0,
        image_detail="auto",
    )


def get_or_create(db: Session) -> LlmSettings:
    row = db.get(LlmSettings, _SINGLETON_ID)
    if row is not None:
        return row
    row = LlmSettings(id=_SINGLETON_ID)
    db.add(row)
    db.flush()
    return row


def to_public(row: LlmSettings) -> LlmSettingsOut:
    key = (row.api_key or "").strip()
    hint = f"····{key[-4:]}" if len(key) >= 4 else ("已配置" if key else None)
    return LlmSettingsOut(
        enabled=row.enabled,
        display_name=row.display_name,
        base_url=row.base_url,
        model_id=row.model_id,
        api_key_configured=bool(key),
        api_key_hint=hint,
        timeout_seconds=row.timeout_seconds,
        max_tokens=row.max_tokens,
        temperature=float(row.temperature),
        image_detail=row.image_detail,  # type: ignore[arg-type]
    )


def apply_update(db: Session, payload: LlmSettingsUpdate) -> tuple[LlmSettings, dict]:
    """返回更新后的行，以及可写入审计的变更摘要（不含密钥原文）。"""
    row = get_or_create(db)
    data = payload.model_dump(exclude_unset=True)
    audit: dict = {}

    if "api_key" in data:
        raw = data.pop("api_key")
        if raw is None:
            pass  # 显式 null：不改密钥
        elif raw:
            row.api_key = raw
            audit["api_key"] = "set"
        else:
            row.api_key = None
            audit["api_key"] = "cleared"

    for field, value in data.items():
        if value is None:
            continue  # 显式 null 一律视为"不修改"
        setattr(row, field, value)
        audit[field] = value

    if row.enabled and (not row.base_url or not row.model_id or not (row.api_key or "").strip()):
        db.rollback()
        raise ValueError("启用模型前需要填写接口地址、模型 ID 和 API Key")

    db.commit()
    db.refresh(row)
    return row, audit


def load_row(db: Session) -> LlmSettings | None:
    return db.scalar(select(LlmSettings).where(LlmSettings.id == _SINGLETON_ID))
