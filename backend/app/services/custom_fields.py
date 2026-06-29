from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Book, CustomField
from app.schemas.custom_field import CustomFieldCreate
from app.utils.db_errors import rollback_on_integrity


@dataclass
class CustomFieldResult:
    field: CustomField
    message: str
    created: bool


def _validate_entity(db: Session, entity_type: str, entity_id: int) -> None:
    if entity_type == "book" and not db.get(Book, entity_id):
        raise ValueError(f"书籍 ID {entity_id} 不存在")


def upsert_custom_field(db: Session, payload: CustomFieldCreate) -> CustomFieldResult:
    _validate_entity(db, payload.entity_type, payload.entity_id)

    existing = db.scalar(
        select(CustomField).where(
            CustomField.entity_type == payload.entity_type,
            CustomField.entity_id == payload.entity_id,
            CustomField.field_key == payload.field_key,
        )
    )
    created = existing is None
    if existing:
        existing.field_value = payload.field_value
        existing.value_type = payload.value_type
        field = existing
    else:
        field = CustomField(
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            field_key=payload.field_key,
            field_value=payload.field_value,
            value_type=payload.value_type,
        )
        db.add(field)

    try:
        db.commit()
    except IntegrityError as exc:
        raise rollback_on_integrity(db, exc) from exc
    db.refresh(field)
    action = "已创建" if created else "已更新"
    return CustomFieldResult(field=field, message=f"自定义字段 {payload.field_key} {action}", created=created)
