"""Telegram adapter — reads tender posts from public Telegram channels via Telethon."""

import logging
import re
import time as _time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from crawler.adapters.base import BaseAdapter
from crawler.config.settings import settings
from crawler.core.models import RawTender, SourceConfig

import httpx as _httpx

logger = logging.getLogger(__name__)

# ── Demand detection: regex patterns for CUSTOMER REQUESTS ──────
# Key insight: PR Media Group is 95% ads from competitors.
# We only want DEMAND (someone LOOKING for a service/product).
# Regex detects demand intent BEFORE wasting AI calls.

_DEMAND_PATTERNS = re.compile(
    r"(?:"
    # ── Russian demand signals ──
    r"кто\s+(?:делает|занимается|производит|печатает|изготавливает|может|шьёт|шьет)"
    r"|ищу\s+(?:поставщик|исполнител|подрядчик|типографи|производител|мастер|работ)"
    r"|(?:нужно|нужны|нужна|нужен)\s+\S+"
    r"|(?:требуется|требуются|необходим[оыа]?)\s+(?!мастер\S*\s+по\s+наруж)\S+"
    r"|кому\s+(?:заказать|обратиться)"
    r"|(?:подскажите|посоветуйте|порекомендуйте)\s+\S+"
    r"|где\s+(?:можно|заказать|найти|купить|напечатать|сделать)"
    r"|есть\s+(?:кто|те\s+кто)"
    r"|(?:срочно|сроч)\s+нужн"
    # ── Uzbek demand signals (Latin) ──
    # "N ta/dona PRODUCT kerak" = нужно N штук (самый надёжный паттерн)
    r"|\d+\s*(?:ta|dona|sht)\s+\S+\s+\S*(?:kerak|zarur|lozim)"
    r"|\d+\s*(?:ta|dona|sht)\s+\S+\s+(?:pechat|bos|tortish)"
    # "PRODUCT kerak/zarur/lozim" — продукт + need-word
    r"|(?:pechat|banner|paket|korobka|etiketka|stiker|vizitka|buklet|futbolka"
    r"|katalog|kitob|bloknot|kalendar|flayer|nakleyk|bayroq|flag"
    r"|quti|gofra|karton|qadoq|yorliq|lenta|plyonka|qogoz|sumka"
    r"|konvert|broshyura|afisha|menyu|sertifikat|diplom|ofset|shtamp"
    r")\S*\s+\S*(?:kera[kq]|zarur|lozim|darkor)"
    # "VERB kerak/zarur" — действие + need-word
    r"|(?:tortish|bosish|chop\s+etish|chop\s+qilish|tayyorlash|qilish|yasash)\s+(?:kera[kq]|zarur|lozim)"
    # "kim qiladi/qilib berolidi/bosadi" = кто сделает (+ все формы)
    r"|kim\s+(?:qiladi|qilib|bosadi|bosib|qiberol|tayyorl|berad|berol|yasay)"
    r"|kim\s+qilib\s+ber"  # "kim qilib berolidi/beradi/bervoring"
    r"|kimda\s+(?:bor|bormi)"  # "kimda bor" = у кого есть
    r"|kimga\s+(?:buyurtma|zakaz)"  # кому заказать
    # "qidiryapman/izlayapman" = ищу
    r"|qidiryap"
    r"|izlayap"
    # "qayerda" = где (+ действие)
    r"|qayerd[a]?\s+(?:bos|tor|chop|qil|ola|topa)"
    # "buyurtma/zakaz bermoqchi/qilmoqchi" = хочу заказать
    r"|buyurtma\s+(?:ber|qil)"
    r"|zakaz\s+(?:qil|ber)"
    # ── NEW: Price inquiry = demand signal ──
    r"|narxi\s+qancha"
    r"|qancha\s+tur(?:adi|idi)"
    r"|narxini\s+ayt"
    # ── NEW: "sotib olmoqchi" = want to buy ──
    r"|sotib\s+ol"
    r"|olmoqchi"
    # ── NEW: Polite request forms ──
    r"|maslahat\s+ber"
    r"|tavsiya\s+qil"
    r"|topib\s+ber"
    r"|tayyorlab\s+ber"
    # ── NEW: "qila oladimi / bera oladimi" ──
    r"|(?:qila|bera|qilib\s*bera)\s+ola"
    # ── NEW: "bormi" with product context ──
    r"|(?:pechat|paket|korobka|quti|etiketka|stiker|vizitka|karton|gofra|qadoq|bloknot)\S*\s+bormi"
    # ── NEW: Urgency markers ──
    r"|shoshilinch"
    r"|(?:bugun|ertaga)\s+(?:kerak|zarur|lozim)"
    # ── NEW: Standalone "kerak" (Latin) — broad but effective ──
    r"|\bkerak\b"
    # ── Uzbek demand signals (Cyrillic) ──
    r"|керак\b"
    r"|зарур\b"
    r"|лозим\b"
    r"|даркор\b"
    r"|кимда\s+бор"
    r"|қидиряпман"
    r"|излаяпман"
    r"|буюртма\s+бер"
    r"|нархи\s+қанча"
    r"|сотиб\s+ол"
    r"|заказ\s+(?:қил|бер)"
    # Кириллический узбекский (разговорный, с ошибками)
    r"|ким\s+(?:қилади|қилиб|босади|босиб|чиқазиб|чиказиб|беролади|беролиди)"
    r"|ким\s+қилиб\s+бер"  # "ким қилиб беролиди"
    r"|(?:босиш|тортиш|чоп\s+этиш|чиқазиш)\s+(?:керак|зарур|лозим)"
    r"|(?:штук|шт|дона|та)\s+\S+\s+(?:керак|зарур|лозим)"
    r")",
    re.IGNORECASE,
)

# False positives: ads, job postings, vacancies, service offers
_QTY_PATTERN = re.compile(
    r"(\d[\d\s.,]*)\s*(?:ta\b|dona\b|sht\b|шт|штук|шт\.|дона|ед\b|единиц)",
    re.IGNORECASE,
)

MIN_QTY_THRESHOLD = 50  # Ignore requests for less than 50 pcs

_AD_FILTER = re.compile(
    r"(?:"
    # ── Vacancies (UZ) ──
    r"pechatnik\s+kerak"  # вакансия печатника
    r"|dizayner\s+kerak"  # вакансия дизайнера
    r"|operator\s+kerak"
    r"|master\s+kerak"
    r"|ishchi\s+kerak"  # нужен работник
    r"|\bish\b\s+kerak"  # ищу работу
    r"|jamoaga\s+\S+\s+kerak"  # "в команду нужен X"
    r"|xizmatlari\b"  # "grafik dizayner xizmatlari" = реклама услуг
    r"|xizmatlar\b"
    # ── Vacancies (RU) ──
    r"|сдельщик"
    r"|сдельн\S+\s+работ"  # "на сдельную работу"
    r"|набор\s+(?:идет|на\s+работу)"
    r"|требуются\s+(?:мастер|сотрудник|работник|специалист|оператор|менеджер)"
    r"|ищу\s+работу"
    r"|ищу\s+заказ"  # фрилансер ищет заказы = не наш клиент
    r"|(?:с\s+\d+\s+до\s+\d+|график\s+работы)"  # "с 9 до 6" = вакансия
    # ── Service ads (RU) — someone OFFERING, not requesting ──
    r"|услуги\s+\S+"  # "Услуги графического дизайнера"
    r"|предлагаем\s+"
    r"|предоставляем\s+"
    r"|выполняем\s+"
    r"|звоните\b"
    r"|обращайтесь\b"
    r"|наши\s+(?:цены|услуги|работы)"
    r"|под\s+заказ\s*[!.]"  # "Печать на лентах под заказ!" = реклама
    r"|beramiz\b"  # "biz bosib beramiz" = мы делаем
    r"|qilamiz\b"  # "biz qilamiz" = мы делаем
    r"|tayyorlaymiz\b"  # мы изготовим
    r"|bosib\s+beramiz"  # "мы напечатаем"
    r"|chop\s+etamiz"  # "мы печатаем"
    r"|murojaat\s+qiling"  # "обращайтесь" (= реклама)
    r"|aloqaga\s+chiqing"  # "свяжитесь" (= реклама)
    r"|arzon\s+narx"  # "дёшево" (= реклама)
    r"|sifatli\S*\s+\S*narx"  # "качественно + цена" (= реклама)
    # ── Not our profile (outdoor advertising, banners, orakal) ──
    r"|баннер|banner"
    r"|оракал|orakal|arakal|аракал"
    r"|наружн\S+\s+реклам"
    r"|монтаж|montaj"
    r"|вывеск|буква.короб|световой.короб"
    r"|фомикс|fomiks|fomeks"
    r"|акрил|alyukobond|алюкобонд"
    r"|плоттер|ploter|широкоформат"
    r"|тонировк|оклейк\S+\s+авто"
    # ── Not our product (tech cards, NFC, textile printing) ──
    r"|nfc"
    r"|(?:elektron|цифров|smart|смарт)\S*\s*визитк"
    # Textile/merch printing — not our profile (полиграфия ≠ текстиль)
    r"|печат\S*\s+на\s+(?:футболк|ткан|текстил|майк|кепк|толстовк|одежд|кружк|бейсболк|худи|свитшот)"
    r"|futbolka\S*\s+(?:pechat|bosish|chop)"
    r"|(?:pechat|bosish|chop)\S*\s+(?:futbolka|kiyim|kurtka|kepka)"
    # Animated/digital stickers — not physical stickers
    r"|анимацион\S+\s+стикер"
    r"|стикер\S*\s+(?:в\s+)?телеграм"
    # ── Off-topic ──
    r"|видеооператор|видеосъемк"
    r"|(?:настро\S+|запуст\S+)\s+рекламу"
    r"|google\s+(?:ads|эдс)"
    r"|instagram\s+(?:uchun|для)\s+post"
    r"|ивент|мероприят"
    r"|нарвон|лесам|stremyank"
    r"|монтажник"
    r"|ремонт\S*\s+принтер"
    # ── Service provider contact info (ads leave phone/username) ──
    r"|\+998\s*\d"  # UZ phone = provider leaving contact
    r"|заказ\s*и\s*консультаци"  # "Заказ и консультация:"
    r"|почему\s+выбирают\s+нас"
    r"|индивидуальн\S+\s+подход"
    r"|ваш[а-яё]*\s+(?:бренд|команд)"  # "Ваш бренд", "Ваша команда"
    r"|изготовление\s+за\s+\d+"  # "Изготовление за 48 часов"
    r"|превращаем\s+"  # "мы превращаем её в результат"
    r"|креатив\s+и\s+стиль"
    r"|(?:ДТФ|DTF).{0,5}(?:винил|печат)"  # "ДТФ-Винил печать"
    r"|шелкограф"  # шелкография
    r"|(?:сила|качество)\s+вашей\s+работы"
    r"|всё.*нужно\s+для\s+ваших"
    r"|нужн\S+\s+качественн\S+\s+печат\S+\s+на\s+\S+[?!]?"  # "Нужна качественная печать на X?" (rhetorical, с ? и без)
    # Marketing slogans
    r"|(?:печатн\S+\s+)?продукци\S+\s+для\s+вашего"  # "Печатная продукция для вашего бизнеса"
    r"|для\s+вашего\s+бизнеса"
    # ── Repeated spam ──
    r"|renova\s+print"
    r"|taklif\b"
    # ── Greetings without substance ──
    r"|^(?:всем\s+)?добр(?:ый|ое|ой|ого)\s+(?:день|утро|вечер|ночи)[!.\s]*$"
    r"|^assalomu?\s+alaykum[!.\s]*$"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# AI prompt — ONLY for confirmed demand messages (after regex pre-filter)
_AI_EXTRACT_PROMPT = """Сообщение из Telegram-группы рекламщиков Узбекистана.

СНАЧАЛА определи — это СПРОС или ПРЕДЛОЖЕНИЕ:

СПРОС (intent: demand) — человек ИЩЕТ кого-то для выполнения работы:
- "Кто делает коробки?" "kim qiladi?" "kerak" (в контексте заказа)
- "Нужен баннер 3x5", "10 ta futbolka pechat kerak"
- Указывает ЧТО нужно, СКОЛЬКО штук, КОГДА

ПРЕДЛОЖЕНИЕ (intent: ad) — человек/компания РЕКЛАМИРУЕТ свои услуги:
- "Мы делаем печать", "Качественная печать на футболках!"
- "Наша компания предлагает...", "TAKLIF!!!", "Звоните!"
- Перечисление СВОИХ услуг, цен, контактов
- "Нужна печать?" с ответом "мы делаем" = ЭТО РЕКЛАМА
- "Эксклюзив совға қутилар" = продают свой товар = РЕКЛАМА
- "ДИЛЕРЛАР УЧУН ТАКЛИФ" = предложение для дилеров = РЕКЛАМА
{few_shot}
Текст:
{text}

Формат (каждое поле на строке):
intent: demand ИЛИ ad
title: что нужно (5-15 слов, только для demand)
organization: кто ищет
price: бюджет (число)
currency: UZS/USD/EUR
deadline: срок (DD.MM.YYYY)
product_keywords: ключевые слова

Если поле не найдено: НЕТ
/no_think"""

# AI prompt for non-group sources (tenders, channels)
_AI_TENDER_PROMPT = """Извлеки из текста тендера/закупки:
- organization: заказчик
- price: сумма (число)
- currency: UZS/USD/EUR
- deadline: дата (DD.MM.YYYY)

Текст:
{text}

Формат (каждое поле на строке):
organization: ...
price: ...
currency: ...
deadline: ...

Если не найдено: НЕТ
/no_think"""


def _ai_extract_fields(text, openrouter_key, model, prompt_template=None, few_shot=""):
    # type: (str, str, str, Optional[str], str) -> dict
    """Use Qwen to extract fields from unstructured text."""
    if not openrouter_key:
        return {}
    template = prompt_template or _AI_EXTRACT_PROMPT
    # Inject few-shot examples if available
    few_shot_block = ""
    if few_shot:
        few_shot_block = "\n\nПримеры из обратной связи пользователя (УЧИТЫВАЙ ИХ):\n%s\n" % few_shot
    try:
        prompt_text = template.format(text=text[:500], few_shot=few_shot_block)
    except KeyError:
        # Template without {few_shot} placeholder (e.g. _AI_TENDER_PROMPT)
        prompt_text = template.format(text=text[:500])
    try:
        resp = _httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer %s" % openrouter_key},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt_text}],
                "max_tokens": 256,
                "temperature": 0,
                # deepseek-v4-* are reasoning models: reasoning eats the token
                # budget and returns EMPTY content (finish_reason=length), which
                # silently disabled the demand-vs-ad classifier. Disable reasoning
                # so this classification task returns a deterministic answer.
                # Verified 2026-06-08 (flash + reasoning off = 3/3 on #2307/8/9).
                "reasoning": {"enabled": False},
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

        # Per-channel time budget. _parse_message runs AI calls (~3-6s each) inline, so
        # fetch time is proportional to backlog size. Without a budget, a multi-day backlog
        # blows the runner's wait_for — the coroutine is CANCELLED mid-loop, the cursor
        # save below never runs, all tenders are discarded, and the next run repeats the
        # same backlog: a livelock that burns OpenRouter spend and persists nothing
        # (2026-07-18: 3 runs x ~60 min of AI calls, 0 saved). Budget-break instead keeps
        # partial results: incremental mode iterates oldest->newest (reverse=True), so
        # max_seen_id = last PROCESSED message and the next run resumes exactly after it.
        budget_s = 90
        t0 = _time.monotonic()

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
                if _time.monotonic() - t0 > budget_s:
                    logger.warning(
                        "[%s] fetch budget %ds exhausted — returning %d tenders, "
                        "cursor at %d, backlog resumes next run",
                        self.config.name, budget_s, len(tenders), max_seen_id,
                    )
                    break

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

    def _is_group_source(self):
        # type: () -> bool
        """Check if this source is a group (numeric ID = private group)."""
        ch = self.config.telegram_channel or ""
        try:
            int(ch)
            return True
        except (ValueError, TypeError):
            return False

    def _parse_message(self, message) -> Optional[RawTender]:  # type: ignore[no-untyped-def]
        """Parse a Telegram message into a RawTender.

        Two modes:
        - GROUP mode (PR Media Group etc): demand-first detection.
          Only messages with DEMAND regex pass → AI extracts details.
          All ads/offers from competitors are silently skipped.
        - CHANNEL mode (tender channels): old regex+AI extraction.
        """
        text = message.text
        if not text or len(text.strip()) < 30:
            return None

        lines = text.strip().split("\n")
        fallback_title = ""
        for line in lines:
            stripped = line.strip()
            if stripped:
                fallback_title = stripped
                break
        if not fallback_title:
            return None

        if self._is_group_source():
            return self._parse_group_message(message, text, fallback_title)
        else:
            return self._parse_channel_message(message, text, fallback_title)

    def _parse_group_message(self, message, text, fallback_title):
        # type: (object, str, str) -> Optional[RawTender]
        """Parse a GROUP message — only DEMAND (someone looking for service).

        Pipeline: demand regex → AI extraction → RawTender(customer_request).
        No demand regex match → skip entirely (it's an ad).
        """
        # Shadow log helper — logs ALL messages for feedback learning
        from crawler.core.feedback import log_message as _log_msg

        def _shadow_log(label):
            # type: (str) -> None
            try:
                _log_msg(self.config.name, message.id, text[:2000], label)
            except Exception:
                pass  # best-effort

        # Step 1: Demand regex — is someone LOOKING for something?
        if not _DEMAND_PATTERNS.search(text):
            # No demand signal → it's an ad/offer from competitor → skip
            _shadow_log("skipped")
            return None

        # Step 1b: Exclude job postings and vacancies
        if _AD_FILTER.search(text):
            logger.debug("[%s] Vacancy/job posting, skipping: %s",
                         self.config.name, fallback_title[:60])
            _shadow_log("ad")
            return None

        # Step 1c: Min quantity filter — skip tiny orders (<50 pcs)
        qty_match = _QTY_PATTERN.search(text)
        if qty_match:
            try:
                qty_str = qty_match.group(1).replace(" ", "").replace(",", ".")
                qty = float(qty_str)
                if 0 < qty < MIN_QTY_THRESHOLD:
                    logger.debug("[%s] Small order (%g pcs < %d), skipping: %s",
                                 self.config.name, qty, MIN_QTY_THRESHOLD,
                                 fallback_title[:60])
                    _shadow_log("low_qty")
                    return None
            except (ValueError, TypeError):
                pass

        logger.info("[%s] Demand detected: %s", self.config.name, fallback_title[:80])

        # Step 2: AI extraction (only for confirmed demand)
        # Inject few-shot examples from user feedback
        from crawler.core.feedback import get_few_shot_examples
        few_shot = get_few_shot_examples(5)

        organization = ""
        price = None  # type: Optional[float]
        currency = "UZS"
        deadline = None  # type: Optional[str]
        ai_title = ""
        product_keywords = ""

        ai_fields = _ai_extract_fields(
            text,
            settings.openrouter_api_key or "",
            settings.ai_relevance_model_fast or settings.ai_relevance_model,
            few_shot=few_shot,
        )
        if ai_fields:
            # AI intent verification — last defense against ads
            intent = ai_fields.get("intent", "").lower().strip()
            if intent == "ad":
                logger.info("[%s] AI: ad (not demand), skipping: %s",
                            self.config.name, fallback_title[:60])
                _shadow_log("ai_ad")
                return None

            if ai_fields.get("title"):
                ai_title = ai_fields["title"][:300]
            if ai_fields.get("organization"):
                organization = ai_fields["organization"][:200]
            if ai_fields.get("price"):
                try:
                    price = float(ai_fields["price"].replace(" ", "").replace(",", "."))
                except (ValueError, TypeError):
                    pass
            if ai_fields.get("currency") and ai_fields["currency"] in ("USD", "EUR"):
                currency = ai_fields["currency"]
            if ai_fields.get("deadline"):
                deadline = ai_fields["deadline"]
            if ai_fields.get("product_keywords"):
                product_keywords = ai_fields["product_keywords"]

        title = ai_title if ai_title else fallback_title

        # Log successful demand detection
        _shadow_log("demand")

        # Build source URL
        channel_raw = (self.config.telegram_channel or "").lstrip("@")
        try:
            chan_id = int(channel_raw)
            source_url = "https://t.me/c/%d/%d" % (chan_id, message.id)
        except (ValueError, TypeError):
            source_url = "https://t.me/%s/%d" % (channel_raw, message.id)

        external_id = str(message.id)
        tender_id = "%s-%s" % (self.config.id_prefix, external_id)

        search_text = " ".join(
            filter(None, [title, organization, text[:1000], product_keywords])
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
            message_type="customer_request",
        )

    def _parse_channel_message(self, message, text, fallback_title):
        # type: (object, str, str) -> Optional[RawTender]
        """Parse a CHANNEL message — standard tender extraction."""
        organization = self._extract_organization(text)
        price, currency = self._extract_price(text)
        deadline = self._extract_deadline(text)

        # AI fallback for missing fields
        if not organization or price is None or not deadline:
            ai_fields = _ai_extract_fields(
                text,
                settings.openrouter_api_key or "",
                settings.ai_relevance_model_fast or settings.ai_relevance_model,
                prompt_template=_AI_TENDER_PROMPT,
            )
            if ai_fields:
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
        channel_raw = (self.config.telegram_channel or "").lstrip("@")
        source_url = "https://t.me/%s/%d" % (channel_raw, message.id)

        external_id = str(message.id)
        tender_id = "%s-%s" % (self.config.id_prefix, external_id)

        search_text = " ".join(
            filter(None, [fallback_title, organization, text[:1000], deadline or ""])
        ).lower()

        return RawTender(
            id=tender_id,
            external_id=external_id,
            title=fallback_title,
            organization=organization,
            price=price,
            currency=currency,
            deadline=deadline,
            source=self.config.name,
            source_url=source_url,
            search_text=search_text,
            message_type="tender",
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
