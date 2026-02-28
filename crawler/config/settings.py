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
    telegram_session: str = "sessions/crawler_session"

    # Telegram alerts (bot token for sending notifications)
    telegram_bot_token: Optional[str] = None
    telegram_alert_chat_id: Optional[str] = None
    alert_keywords: str = "упаковка,полиграфия,гофра,коробка,печать,этикетка,типография,книга,книж,каталог,брошюр,блокнот,календар,пакет,конверт,папка,ежедневник,сувенир,журнал,картон,подарочн,зонт,ручка,флешк,power bank,набор,плакат,постер,стенд,вывеск,packaging,printing,cardboard,label,box,qadoqlash,bosma"

    # AI relevance filter (OpenRouter / Qwen)
    openrouter_api_key: Optional[str] = None
    ai_relevance_model: str = "qwen/qwen3-30b-a3b"

    # Crawler behaviour
    dry_run: bool = False
    log_level: str = "INFO"
    batch_size: int = 500

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
