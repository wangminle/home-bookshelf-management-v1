from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomFieldCreate(BaseModel):
    entity_type: str = Field(description="book | copy | member 等")
    entity_id: int
    field_key: str = Field(min_length=1, max_length=100)
    field_value: str | None = None
    value_type: str = Field(default="string")


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
