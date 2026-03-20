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

# AI extraction prompt — classifies message type AND extracts fields
_AI_EXTRACT_PROMPT = """Классифицируй сообщение из Telegram-канала и извлеки данные.

Шаг 1 — Определи тип:
- tender: объявление о закупке/тендере (есть заказчик, требования, лот)
- customer_request: запрос потенциального клиента ("кто делает...", "нужны...", "ищем поставщика...", "требуется...")
- info: новость, реклама, объявление (не тендер и не запрос)
- spam: мусор, только приветствие, нет содержания

Шаг 2 — Извлеки поля:

Примеры:
Сообщение: "Ассалому алайкум! Кто делает коробки из гофрокартона? Нужно 5000 шт, размер 30x20x15. Бюджет обсуждаемо. Тел: +998901234567"
→ message_type: customer_request
→ title: Запрос на коробки из гофрокартона 5000 шт
→ product_keywords: коробки, гофрокартон, упаковка

Сообщение: "Тендер №45-2026. Закупка этикеток для АО Узфармсаноат. Сумма: 150 000 000 сум. Дедлайн: 25.04.2026"
→ message_type: tender
→ title: Закупка этикеток для АО Узфармсаноат
→ organization: АО Узфармсаноат
→ price: 150000000
→ currency: UZS
→ deadline: 25.04.2026
→ product_keywords: этикетки, печать

Сообщение: "Кому нужна полиграфия? Печатаем каталоги, буклеты, визитки. Звоните!"
→ message_type: info
→ title: Предложение полиграфических услуг
→ product_keywords: полиграфия, каталоги, буклеты, визитки

Текст сообщения:
{text}

Ответь СТРОГО в формате (каждое поле на отдельной строке):
message_type: ...
title: ...
organization: ...
price: ...
currency: ...
deadline: ...
product_keywords: ...

Если поле не найдено: НЕТ
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
        """Connect to Telegram, read channel messages, parse tenders.

        Uses incremental collection: tracks last_message_id per channel
        so we only fetch NEW messages each run (inspired by tg_content_factory).
        Handles FloodWait gracefully instead of skipping.
        """
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            logger.warning(
                "[%s] Telegram credentials not set (TELEGRAM_API_ID, "
                "TELEGRAM_API_HASH). Skipping.",
                self.config.name,
            )
            return []

        import asyncio
        import os

        from telethon import TelegramClient
        from telethon.errors import FloodWaitError, ChannelPrivateError

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

        # Load last_message_id for incremental collection
        last_id = self._load_last_message_id()

        try:
            await client.connect()

            if not await client.is_user_authorized():
                logger.error(
                    "[%s] Telegram session not authorized. "
                    "Run 'python3 crawler/auth_telegram.py' first.",
                    self.config.name,
                )
                return []

            channel_raw = self.config.telegram_channel or ""
            # Support numeric IDs for private groups (no username)
            try:
                channel = int(channel_raw)  # type: ignore[assignment]
            except (ValueError, TypeError):
                channel = channel_raw
                if channel and not channel.startswith("@"):
                    channel = "@" + channel

            # Incremental: if we have last_id, only get newer messages
            # Otherwise fall back to limit-based collection
            iter_kwargs = {}  # type: dict
            if last_id > 0:
                iter_kwargs["min_id"] = last_id
                iter_kwargs["reverse"] = True  # oldest first when using min_id
                logger.info(
                    "[%s] Incremental: fetching messages after ID %d",
                    self.config.name, last_id,
                )
            else:
                iter_kwargs["limit"] = self.config.telegram_limit
                logger.info(
                    "[%s] First run: fetching last %d messages",
                    self.config.name, self.config.telegram_limit,
                )

            max_seen_id = last_id

            async for message in client.iter_messages(channel, **iter_kwargs):
                # Track highest message ID for next incremental run
                if message.id > max_seen_id:
                    max_seen_id = message.id

                # Skip non-text messages
                if not message.text:
                    continue

                # Skip messages older than cutoff (only for non-incremental)
                if last_id == 0:
                    msg_date = message.date
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)
                    if msg_date < cutoff:
                        break

                tender = self._parse_message(message)
                if tender is not None:
                    tenders.append(tender)

            # Save last_message_id for next run
            if max_seen_id > last_id:
                self._save_last_message_id(max_seen_id)

        except FloodWaitError as e:
            wait_secs = e.seconds
            if wait_secs <= 120:
                logger.warning(
                    "[%s] FloodWait %ds — waiting...",
                    self.config.name, wait_secs,
                )
                await asyncio.sleep(wait_secs)
                # Don't retry here — will be picked up next cron cycle
            else:
                logger.warning(
                    "[%s] FloodWait %ds (>2min) — skipping this cycle",
                    self.config.name, wait_secs,
                )
        except ChannelPrivateError:
            logger.warning(
                "[%s] Channel is private or deleted — consider disabling",
                self.config.name,
            )
        except Exception as exc:
            logger.warning(
                "[%s] Telegram error: %s", self.config.name, str(exc)
            )
        finally:
            await client.disconnect()

        return tenders

    def _load_last_message_id(self):
        # type: () -> int
        """Load last collected message ID from file-based cache."""
        import os
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cache",
        )
        cache_file = os.path.join(cache_dir, "tg_last_id_%s.txt" % self.config.id)
        try:
            with open(cache_file, "r") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def _save_last_message_id(self, msg_id):
        # type: (int) -> None
        """Save last collected message ID to file-based cache."""
        import os
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cache",
        )
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, "tg_last_id_%s.txt" % self.config.id)
        with open(cache_file, "w") as f:
            f.write(str(msg_id))

    def _parse_message(self, message) -> Optional[RawTender]:  # type: ignore[no-untyped-def]
        """Parse a Telegram message into a RawTender.

        Uses regex for structured fields, then AI for classification,
        title extraction, and missing field recovery.
        """
        text = message.text
        if not text or len(text.strip()) < 10:
            return None

        lines = text.strip().split("\n")

        # Fallback title: first non-empty line
        fallback_title = ""
        for line in lines:
            stripped = line.strip()
            if stripped:
                fallback_title = stripped
                break

        if not fallback_title:
            return None

        # Organization (regex first)
        organization = self._extract_organization(text)

        # Price (regex first)
        price, currency = self._extract_price(text)

        # Deadline (regex first)
        deadline = self._extract_deadline(text)

        # AI extraction: classify message type + extract/improve fields
        message_type = "tender"
        ai_title = ""
        product_keywords = ""

        ai_fields = _ai_extract_fields(
            text,
            settings.openrouter_api_key or "",
            settings.ai_relevance_model,
        )

        if ai_fields:
            # Message type classification
            msg_type = ai_fields.get("message_type", "").lower().strip()
            if msg_type in ("tender", "customer_request", "info", "spam"):
                message_type = msg_type

            # Skip spam
            if message_type == "spam":
                logger.debug("[%s] AI classified as spam, skipping: %s",
                             self.config.name, fallback_title[:60])
                return None

            # AI-extracted title (the ESSENCE, not first line)
            if ai_fields.get("title"):
                ai_title = ai_fields["title"][:300]

            # Fill missing fields from AI
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

            # Product keywords for search_text enrichment
            if ai_fields.get("product_keywords"):
                product_keywords = ai_fields["product_keywords"]

        # Use AI title if available, fallback to first line
        title = ai_title if ai_title else fallback_title

        # Build source URL (supports both usernames and numeric IDs)
        channel_raw = (self.config.telegram_channel or "").lstrip("@")
        try:
            # Numeric ID → private group link format
            chan_id = int(channel_raw)
            source_url = "https://t.me/c/%d/%d" % (chan_id, message.id)
        except (ValueError, TypeError):
            source_url = "https://t.me/%s/%d" % (channel_raw, message.id)

        external_id = str(message.id)
        tender_id = "%s-%s" % (self.config.id_prefix, external_id)

        # search_text: full message body + AI keywords (not just title+org)
        search_text = " ".join(
            filter(None, [
                title,
                organization,
                text[:1000],
                product_keywords,
                deadline or "",
            ])
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
            message_type=message_type,
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
