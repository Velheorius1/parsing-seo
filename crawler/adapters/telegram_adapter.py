"""Telegram adapter — reads tender posts from public Telegram channels via Telethon."""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from crawler.adapters.base import BaseAdapter
from crawler.config.settings import settings
from crawler.core.models import RawTender, SourceConfig

import httpx as _httpx

logger = logging.getLogger(__name__)

# AI extraction prompt for unstructured Telegram messages
_AI_EXTRACT_PROMPT = """Извлеки из текста тендера/закупки следующие поля (если есть):
- organization: название заказчика/организации
- price: сумма в числовом формате (только число)
- currency: валюта (UZS/USD/EUR)
- deadline: дата дедлайна (формат DD.MM.YYYY)

Текст:
{text}

Ответь СТРОГО в формате (каждое поле на отдельной строке):
organization: ...
price: ...
currency: ...
deadline: ...

Если поле не найдено, пиши: НЕТ
/no_think"""


def _ai_extract_fields(text, openrouter_key, model):
    # type: (str, str, str) -> dict
    """Use Qwen to extract org/price/deadline from unstructured text."""
    if not openrouter_key:
        return {}
    try:
        resp = _httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer %s" % openrouter_key},
            json={
                "model": model,
                "messages": [{"role": "user", "content": _AI_EXTRACT_PROMPT.format(text=text[:500])}],
                "max_tokens": 150,
                "temperature": 0,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return {}
        import re as _re
        answer = resp.json()["choices"][0]["message"]["content"] or ""
        # Strip thinking tags
        answer = _re.sub(r"<think>.*?</think>", "", answer, flags=_re.DOTALL).strip()

        result = {}
        for line in answer.split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            if val and val != "НЕТ":
                result[key] = val
        return result
    except Exception:
        return {}


# Patterns for extracting tender fields from message text
_PRICE_PATTERN = re.compile(
    r"(?:Сумма|Цена|Стоимость|Бюджет|Price|Начальная цена)[:\s]*"
    r"([\d\s.,]+)\s*(UZS|USD|EUR|сум|сўм|\$)?",
    re.IGNORECASE,
)
_PRICE_FALLBACK = re.compile(
    r"([\d]{1,3}(?:[\s.,][\d]{3})+(?:[.,]\d{1,2})?)\s*(UZS|USD|EUR|сум|сўм)?",
)
_ORG_PATTERN = re.compile(
    r"(?:Заказчик|Организация|Компания|Закупщик|Покупатель|Customer)[:\s]*(.+)",
    re.IGNORECASE,
)
_DEADLINE_PATTERN = re.compile(
    r"(?:Дедлайн|Срок|До|Deadline|Дата окончания|Окончание)[:\s]*"
    r"(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4})",
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(
    r"(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4})",
)

# Max age of messages to process
MAX_AGE_DAYS = 7


class TelegramAdapter(BaseAdapter):
    """Fetch tenders from Telegram channels using Telethon."""

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        if not config.telegram_channel:
            raise ValueError(
                "Telegram adapter requires telegram_channel (source: %s)"
                % config.id
            )

    async def _fetch_items(self) -> List[RawTender]:
        """Connect to Telegram, read channel messages, parse tenders."""
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            logger.warning(
                "[%s] Telegram credentials not set (TELEGRAM_API_ID, "
                "TELEGRAM_API_HASH). Skipping.",
                self.config.name,
            )
            return []

        import os

        from telethon import TelegramClient

        # Resolve session path relative to crawler package dir
        session_path = settings.telegram_session
        if not os.path.isabs(session_path):
            pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            session_path = os.path.join(pkg_dir, session_path)
        client = TelegramClient(
            session_path,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

        tenders = []  # type: List[RawTender]
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

        try:
            await client.connect()

            if not await client.is_user_authorized():
                logger.error(
                    "[%s] Telegram session not authorized. "
                    "Run 'python3 crawler/auth_telegram.py' first.",
                    self.config.name,
                )
                return []

            channel = self.config.telegram_channel
            # Normalize channel name
            if channel and not channel.startswith("@"):
                channel = "@" + channel

            async for message in client.iter_messages(
                channel, limit=self.config.telegram_limit
            ):
                # Skip non-text messages
                if not message.text:
                    continue

                # Skip messages older than cutoff
                msg_date = message.date
                if msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                if msg_date < cutoff:
                    break  # Messages are in reverse chronological order

                tender = self._parse_message(message)
                if tender is not None:
                    tenders.append(tender)

        except Exception as exc:
            logger.warning(
                "[%s] Telegram error: %s", self.config.name, str(exc)
            )
        finally:
            await client.disconnect()

        return tenders

    def _parse_message(self, message) -> Optional[RawTender]:  # type: ignore[no-untyped-def]
        """Parse a Telegram message into a RawTender."""
        text = message.text
        if not text or len(text.strip()) < 10:
            return None

        lines = text.strip().split("\n")

        # Title: first non-empty line
        title = ""
        for line in lines:
            stripped = line.strip()
            if stripped:
                title = stripped
                break

        if not title:
            return None

        # Organization (regex first, AI fallback)
        organization = self._extract_organization(text)

        # Price (regex first)
        price, currency = self._extract_price(text)

        # Deadline (regex first)
        deadline = self._extract_deadline(text)

        # AI fallback: if regex missed org/price/deadline, try Qwen
        if not organization or price is None or not deadline:
            ai_fields = _ai_extract_fields(
                text,
                settings.openrouter_api_key or "",
                settings.ai_relevance_model,
            )
            if not organization and ai_fields.get("organization"):
                organization = ai_fields["organization"][:200]
            if price is None and ai_fields.get("price"):
                try:
                    price = float(ai_fields["price"].replace(" ", "").replace(",", "."))
                except (ValueError, TypeError):
                    pass
            if ai_fields.get("currency") and ai_fields["currency"] in ("USD", "EUR"):
                currency = ai_fields["currency"]
            if not deadline and ai_fields.get("deadline"):
                deadline = ai_fields["deadline"]

        # Build source URL
        channel_name = (self.config.telegram_channel or "").lstrip("@")
        source_url = "https://t.me/%s/%d" % (channel_name, message.id)

        external_id = str(message.id)
        tender_id = "%s-%s" % (self.config.id_prefix, external_id)

        search_text = " ".join(
            filter(None, [title, organization, deadline or ""])
        ).lower()

        return RawTender(
            id=tender_id,
            external_id=external_id,
            title=title,
            organization=organization,
            price=price,
            currency=currency,
            deadline=deadline,
            source=self.config.name,
            source_url=source_url,
            search_text=search_text,
        )

    @staticmethod
    def _extract_organization(text: str) -> str:
        """Extract organization name from message text."""
        match = _ORG_PATTERN.search(text)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_price(text: str) -> tuple:
        """Extract price and currency from message text.

        Returns (price_float_or_None, currency_str).
        """
        # Try labeled pattern first
        match = _PRICE_PATTERN.search(text)
        if not match:
            match = _PRICE_FALLBACK.search(text)

        if not match:
            return None, "UZS"

        num_str = match.group(1).replace(" ", "").replace(",", ".")
        # Handle thousand-separator dots: 1.234.567
        parts = num_str.split(".")
        if len(parts) > 2:
            num_str = "".join(parts)

        try:
            price = float(num_str)
        except ValueError:
            return None, "UZS"

        currency = "UZS"
        if match.lastindex and match.lastindex >= 2 and match.group(2):
            cur = match.group(2).upper().strip()
            if cur in ("USD", "$"):
                currency = "USD"
            elif cur in ("EUR",):
                currency = "EUR"
            # Otherwise default UZS

        return price, currency

    @staticmethod
    def _extract_deadline(text: str) -> Optional[str]:
        """Extract deadline date from message text."""
        match = _DEADLINE_PATTERN.search(text)
        if match:
            return match.group(1)
        # Fallback: look for any date if the word "deadline" etc. is absent
        # Only use if there's a single date in the text
        dates = _DATE_PATTERN.findall(text)
        if len(dates) == 1:
            return dates[0]
        return None
