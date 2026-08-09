from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Home Bookshelf API"
    debug: bool = False
    database_url: str = "sqlite:///./data/bookshelf.db"
    data_dir: Path = Path("./data")
    google_books_api_key: str | None = None
    metadata_user_agent: str = "home-bookshelf/1.0"
    # 非空库时匿名 bind 的可选管理口令（请求头 X-Setup-Token）
    setup_token: str | None = None
    # BUG-132：可选的渠道头 HMAC 共享密钥。配置后，携带 X-Channel/X-External-User-Id
    # 的请求必须附带 X-Channel-Signature = HMAC-SHA256(secret, "{channel}:{external_user_id}")
    # 的十六进制摘要，防止局域网内伪造明文渠道头冒充已绑定成员。
    # 不配置则维持"可信局域网/网关代填头"的既有信任边界。
    channel_signing_secret: str | None = None

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"


settings = Settings()
