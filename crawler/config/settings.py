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
    alert_keywords: str = "упаковка,полиграфия,гофра,гофрокоробка,печать полиграф,этикетка,типография,книга,книж,каталог,брошюр,блокнот,календар,полиэтилен пакет,конверт,папка,ежедневник,сувенирная продукция,журнал печать,картон,подарочн,зонт,ручка,флешк,power bank,плакат,постер,стенд выставочн,вывеск,packaging,printing,cardboard,label,qadoqlash,bosma,pechat,paket,korobka,etiketka,banner,futbolka,katalog,kitob,bloknot,kalendar,stiker,nakleyk,vizitka,buklet,listovk,flayer"

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
