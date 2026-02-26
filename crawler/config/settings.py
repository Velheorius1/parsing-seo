"""Application settings loaded from environment variables."""

from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All config from environment."""

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Telegram (for Telethon adapter)
    telegram_api_id: Optional[int] = None
    telegram_api_hash: Optional[str] = None
    telegram_session: str = "crawler_session"

    # Crawler behaviour
    dry_run: bool = False
    log_level: str = "INFO"
    batch_size: int = 500

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
