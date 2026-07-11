from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CustomFieldEntityType = Literal["book", "copy", "member", "note"]
CUSTOM_FIELD_ENTITY_TYPES = frozenset(("book", "copy", "member", "note"))


class CustomFieldCreate(BaseModel):
    entity_type: CustomFieldEntityType = Field(description="book | copy | member | note")
    entity_id: int
    field_key: str = Field(min_length=1, max_length=100)
    field_value: str | None = None
    value_type: str = Field(default="string")

    @field_validator("entity_id")
    @classmethod
    def _validate_entity_id(cls, v):
        if not isinstance(v, int) or v <= 0:
            raise ValueError("entity_id 必须为正整数")
        return v


class CustomFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: int
    field_key: str
    field_value: str | None = None
    value_type: str
    created_at: datetime
    message: str = ""