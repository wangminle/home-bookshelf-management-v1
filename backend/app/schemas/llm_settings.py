from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

ImageDetail = Literal["auto", "low", "high"]


class LlmSettingsOut(BaseModel):
    enabled: bool
    display_name: str
    base_url: str
    model_id: str
    api_key_configured: bool
    api_key_hint: str | None = None
    timeout_seconds: int
    max_tokens: int
    temperature: float
    image_detail: ImageDetail


class LlmSettingsUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, max_length=500)
    model_id: str | None = Field(default=None, max_length=128)
    # 省略或 null：不改密钥。空字符串：清除密钥。
    api_key: str | None = Field(default=None, max_length=512)
    timeout_seconds: int | None = Field(default=None, ge=5, le=180)
    max_tokens: int | None = Field(default=None, ge=16, le=8192)
    temperature: float | None = Field(default=None, ge=0, le=2)
    image_detail: ImageDetail | None = None

    @field_validator("display_name", "model_id", "api_key")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if "\n" in cleaned or "\r" in cleaned:
            raise ValueError("不能包含换行")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return ""
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("接口地址须为 http 或 https URL")
        if parsed.username or parsed.password:
            raise ValueError("不要把密钥写进接口地址")
        return cleaned.rstrip("/")
