from crawler.adapters.base import BaseAdapter
from crawler.adapters.spa import SpaAdapter

__all__ = ["BaseAdapter", "SpaAdapter"]

# Telegram adapter is optional — file does not exist yet (planned feature)
try:
    from crawler.adapters.telegram_adapter import TelegramAdapter
    __all__.append("TelegramAdapter")
except ImportError:
    pass
