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

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"


settings = Settings()
