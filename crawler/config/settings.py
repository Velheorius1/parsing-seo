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
        # Сувенирная продукция
        "кружк", "термос", "термокружк", "бейдж",
        "медал", "кубок", "грамот", "подарочн",
        "сувенир", "новогодн", "корпоратив", "промо", "мерч",
        "магнит", "брелок", "значок", "шоппер",
        # English
        "packaging", "printing", "cardboard", "label", "sticker",
        "souvenir", "promotional", "gift", "badge",
    ])

    # AI relevance filter (OpenRouter / Qwen)
    openrouter_api_key: Optional[str] = None
    # Hybrid mode: fast model checks first; if it rejects → second opinion from
    # max model. If fast accepts → trust it (saves ~99% of tokens). Set
    # ai_relevance_model_fast to empty string to disable hybrid and use only
    # ai_relevance_model.
    #
    # 2026-05-19: switched fast from qwen/qwen3-30b-a3b → deepseek/deepseek-v4-flash
    # (paid: $0.112/$0.224 per M tokens) for A/B comparison. JSONL logs at
    # /var/log/parsing-seo-ai-decisions.jsonl (analyse via scripts/compare_ai_models.py).
    # Override via env AI_RELEVANCE_MODEL_FAST=qwen/qwen3-30b-a3b to roll back
    # without redeploy.
    ai_relevance_model_fast: str = "deepseek/deepseek-v4-flash"
    # 2026-05-26: max model switched qwen/qwen3.6-max-preview → deepseek/deepseek-v4-pro
    # after permanent 75% discount ($0.435/$0.87 per M vs Qwen ~$2/$8). Pro is also
    # ~5-7x faster than Qwen3.6-max-preview p95=42s (was hurting alert latency).
    # Override via env AI_RELEVANCE_MODEL=qwen/qwen3.6-max-preview to roll back.
    ai_relevance_model: str = "deepseek/deepseek-v4-pro"
    # Structured AI output (migration 017): minimum score 0-100 to pass filter.
    # 70 = "вероятно наш". Lower = noisier alerts, higher = miss edge cases.
    ai_score_threshold: int = 70

    # AI evaluator (daily quality report) — DeepSeek V4 Pro for nuanced analysis
    # of crawl quality stats. Falls back to template-based recommendations if
    # LLM call fails (network / 402 / parse). Set ai_eval_enabled=false to skip.
    ai_eval_enabled: bool = True
    ai_evaluator_model: str = "deepseek/deepseek-v4-pro"

    # Crawler behaviour
    dry_run: bool = False
    log_level: str = "INFO"
    batch_size: int = 500

    # Proxy (residential proxy for geo-blocked sources)
    proxy_secret: Optional[str] = None
    cooperation_proxy_url: str = "https://parsing-seo.vercel.app/api/proxy/cooperation"
    residential_proxy_url: Optional[str] = None  # socks5://user:pass@host:port

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
