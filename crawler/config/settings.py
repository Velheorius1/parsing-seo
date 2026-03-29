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
    alert_keywords: str = ",".join([
        # Русские
        "упаковка", "полиграфия", "гофра", "гофрокоробка", "печать", "этикетка",
        "типография", "книга", "книж", "каталог", "брошюр", "блокнот", "календар",
        "пакет", "конверт", "папка", "ежедневник", "сувенирн", "картон", "подарочн",
        "зонт", "ручка", "флешк", "плакат", "постер", "стенд", "вывеск",
        "визитк", "буклет", "листовк", "флаер", "наклейк", "стикер",
        "футболк", "флаг", "ленты",
        "ламинир", "сублимац", "коробк", "дтф", "dtf",
        # Узбекские (латиница)
        "pechat", "paket", "korobka", "karobka", "etiketka", "futbolka",
        "katalog", "kitob", "bloknot", "kalendar", "stiker", "nakleyk", "vizitka",
        "buklet", "listovk", "flayer", "orakal", "arakal", "laminat",
        "sublimats", "lenta", "quti", "sovga",
        "bosma", "qadoqlash", "qadoq", "quti", "gofra", "karton",
        "yorliq", "plyonka", "qogoz", "konvert", "afisha", "menyu",
        "sertifikat", "diplom", "ofset", "shtamp", "chop",
        "kerak", "zarur", "lozim",
        # Узбекские (кириллица)
        "босма", "қадоқлаш", "қути", "совга", "совға",
        "коробка", "керак", "зарур", "лозим", "ёрлиқ",
        # English
        "packaging", "printing", "cardboard", "label", "sticker",
    ])

    # AI relevance filter (OpenRouter / Qwen)
    openrouter_api_key: Optional[str] = None
    ai_relevance_model: str = "qwen/qwen3-30b-a3b"

    # AI evaluator (daily quality report)
    ai_eval_enabled: bool = True

    # Crawler behaviour
    dry_run: bool = False
    log_level: str = "INFO"
    batch_size: int = 500

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def validate_settings():
    # type: () -> None
    """Warn about missing critical environment variables."""
    import logging
    _logger = logging.getLogger(__name__)
    missing = []
    if not settings.supabase_url:
        missing.append("SUPABASE_URL")
    if not settings.supabase_service_role_key:
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    if missing:
        _logger.warning("Missing critical env vars: %s — DB writes will be skipped", ", ".join(missing))
    if not settings.telegram_bot_token:
        _logger.info("TELEGRAM_BOT_TOKEN not set — alerts disabled")
    if not settings.openrouter_api_key:
        _logger.info("OPENROUTER_API_KEY not set — AI enrichment/relevance disabled")
