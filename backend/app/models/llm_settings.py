"""Owner 在后台保存的多模态模型接口配置（单行）。"""

from sqlalchemy import Boolean, Float, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampUpdateMixin


class LlmSettings(Base, TimestampUpdateMixin):
    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(80), default="识书模型", server_default="识书模型", nullable=False
    )
    base_url: Mapped[str] = mapped_column(String(500), default="", server_default="", nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), default="", server_default="", nullable=False)
    # 只写入、不回显。GET 只给末四位提示。
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=1024, server_default="1024", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    # OpenAI 兼容识图精细度：auto / low / high
    image_detail: Mapped[str] = mapped_column(String(8), default="auto", server_default="auto", nullable=False)
