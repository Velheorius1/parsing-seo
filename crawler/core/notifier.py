"""Telegram alert notifier — sends new matching tenders to a Telegram chat.

Pipeline: deadline filter → keyword match → AI relevance check (Qwen via OpenRouter) → send.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

# ── AI relevance filter ──────────────────────────────────────────

_RELEVANCE_PROMPT = """Наша компания — ТИПОГРАФИЯ и УПАКОВОЧНОЕ производство в Узбекистане.

МЫ ДЕЛАЕМ (YES):
- Коробки (гофро, картон, подарочные, совга кутилар)
- Этикетки, стикеры, наклейки
- Полиграфия (каталоги, книги, брошюры, блокноты, визитки, буклеты)
- Пакеты (полиэтилен, крафт)
- Баннеры, постеры, плакаты (bosma, pechat)
- Сувенирная продукция (ручки, флешки, ежедневники, кружки)
- Печать на футболках, флагах, лентах, ткани (DTF, сублимация)
- Оракал, плоттерная резка
- UV печать (на фомиксе, пластике, стекле)
- Ламинирование, переплёт
- Пластиковые карты (скидочные, дисконтные)

НЕ НАШЕ (NO):
- Баннеры, широкоформатная печать, оракал, плоттерная резка
- Наружная реклама, вывески, буквы, монтаж, световые короба
- Оклейка авто, тонировка, плёнки
- Фомикс, акрил, алюкобонд (рекламные конструкции)
- Вакансии, поиск работы, набор сотрудников
- Видеосъёмка, фото, SMM, реклама в Google/Instagram
- IT-услуги, разработка сайтов
- Организация мероприятий, ивенты
- Стройматериалы (лестницы, леса)
- Мебель, оборудование, станки, ремонт принтеров
- Только дизайн без печати
- Реклама ЧУЖИХ услуг ("мы делаем...", "звоните нам...")
- Мелкий заказ (менее 50 штук)

Объявление:
Название: {title}
Заказчик: {organization}

Ответь YES или NO (одно слово).
/no_think"""


async def _ai_check_relevance(
    tender: RawTender,
    client: httpx.AsyncClient,
) -> bool:
    """Check tender relevance via Qwen (OpenRouter). Returns True if relevant."""
    if not settings.openrouter_api_key:
        return True  # no key = skip filter, send all

    prompt = _RELEVANCE_PROMPT.format(
        title=tender.title[:300],
        organization=tender.organization or "",
    )

    try:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer %s" % settings.openrouter_api_key,
            },
            json={
                "model": settings.ai_relevance_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 20,
                "temperature": 0,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("[AI Filter] OpenRouter %d: %s", resp.status_code, resp.text[:100])
            return True  # on error, let it through

        data = resp.json()
        raw_answer = data["choices"][0]["message"]["content"] or ""
        # Strip Qwen3 thinking tags if present
        answer = raw_answer.strip()
        if "<think>" in answer:
            # Remove <think>...</think> block
            import re as _re
            answer = _re.sub(r"<think>.*?</think>", "", answer, flags=_re.DOTALL).strip()
        answer = answer.upper()
        if not answer:
            return True  # empty answer = let it through
        is_relevant = answer.startswith("YES")
        if not is_relevant:
            logger.info("[AI Filter] REJECTED: %s (answer=%s)", tender.title[:60], answer)
        return is_relevant

    except Exception as exc:
        logger.warning("[AI Filter] Error: %s", str(exc)[:80])
        return True  # on error, let it through

# ── Deadline filter ──────────────────────────────────────────────

# Common date patterns in tender deadlines
_DATE_PATTERNS = [
    (re.compile(r"(\d{2})\.(\d{2})\.(\d{4})"), "%d.%m.%Y"),   # 15.05.2023
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "%Y-%m-%d"),     # 2023-05-15
    (re.compile(r"(\d{2})/(\d{2})/(\d{4})"), "%d/%m/%Y"),     # 15/05/2023
]


def _parse_deadline(deadline_str: Optional[str]) -> Optional[datetime]:
    """Try to parse a deadline string into a datetime. Returns None if unparseable."""
    if not deadline_str:
        return None
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(deadline_str)
        if m:
            try:
                return datetime.strptime(m.group(0), fmt)
            except ValueError:
                continue
    return None


def _is_deadline_expired(tender: RawTender) -> bool:
    """Check if tender deadline has already passed. Returns False if no deadline."""
    dt = _parse_deadline(tender.deadline)
    if dt is None:
        return False  # no deadline or unparseable = let it through
    return dt < datetime.utcnow() - timedelta(days=1)  # 1 day grace period


# Minimum stem length for fuzzy matching (Russian word roots)
_MIN_STEM = 4


def _get_keywords() -> List[str]:
    """Parse comma-separated keywords from settings."""
    raw = settings.alert_keywords or ""
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _stem(word: str) -> str:
    """Crude Russian stemming — trim to root for matching.

    'упаковка' -> 'упаков', 'полиграфия' -> 'полиграф', 'печать' -> 'печат'
    Short words (<= _MIN_STEM) kept as-is.
    """
    if len(word) <= _MIN_STEM:
        return word
    # Strip common Russian suffixes (longest first)
    for suffix in ("ция", "ия", "ка", "ок", "ей", "ов", "ть", "ые", "ой", "ая", "ое", "а", "о", "е", "и", "у", "ы"):
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def _word_start_match(text: str, stem: str) -> int:
    """Find stem at word boundary (start of word). Return index or -1."""
    start = 0
    while True:
        idx = text.find(stem, start)
        if idx == -1:
            return -1
        # Stem must be at start of a word: preceded by non-alpha or string start
        if idx == 0 or not text[idx - 1].isalpha():
            return idx
        start = idx + 1


# False positive patterns: if stem is followed by these strings, skip the match
_FALSE_POSITIVES = {
    "календар": [" кун", "кун ", " дн", " день"],  # "календарных дней/кун" = time, not product
}


def _find_matching_keyword(tender: RawTender, keywords: List[str]) -> Optional[str]:
    """Return first matching keyword or None.

    Uses stem-based matching with word-boundary check to avoid
    false positives like 'зонт' in 'горизонтал'.
    """
    text = (tender.search_text + " " + tender.title).lower()
    for kw in keywords:
        stem = _stem(kw) if len(kw) > _MIN_STEM else kw

        if len(stem) < _MIN_STEM:
            # Short keywords: exact match only
            if _word_start_match(text, kw) >= 0:
                return kw
            continue

        idx = _word_start_match(text, stem)
        if idx < 0:
            continue

        # Check false positive exclusions
        excl = _FALSE_POSITIVES.get(stem)
        if excl:
            after = text[idx + len(stem):idx + len(stem) + 10]
            if any(after.startswith(fp) for fp in excl):
                continue

        return kw
    return None


def _escape_md(text: str) -> str:
    """Escape Markdown special chars for Telegram."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, "")
    return text


def _format_alert(
    tender: RawTender,
    matched_kw: str,
    extra_sources: Optional[List[str]] = None,
) -> str:
    """Format a single tender alert message for Telegram."""
    parts = []
    # Prefix by message type
    if tender.message_type == "customer_request":
        parts.append("[ЗАПРОС КЛИЕНТА]")
    elif tender.message_type == "info":
        parts.append("[ИНФО]")
    parts.append("*%s*" % _escape_md(tender.title[:200]))
    if tender.organization:
        parts.append("Заказчик: %s" % _escape_md(tender.organization))
    if tender.price:
        parts.append("Сумма: %s %s" % ("{:,.0f}".format(tender.price), tender.currency))
    if tender.deadline:
        parts.append("Дедлайн: %s" % tender.deadline)
    # Show all sources if tender found on multiple platforms
    if extra_sources and len(extra_sources) > 1:
        parts.append("Площадки (%d): %s" % (len(extra_sources), ", ".join(extra_sources)))
    else:
        parts.append("Источник: %s" % tender.source)
    if tender.source_url:
        parts.append(tender.source_url)
    parts.append("#%s" % matched_kw.replace(" ", "_"))
    return "\n".join(parts)


async def send_alerts(
    new_tenders: List[RawTender],
    dry_run: bool = False,
    group_sources: Optional[Dict[str, List[str]]] = None,
) -> int:
    """Filter tenders by keywords and send Telegram alerts.

    Args:
        new_tenders: List of tenders that were newly inserted (not seen before).
        dry_run: If True, log but don't send.
        group_sources: Optional dict {tender.id: [source_names]} for cross-source groups.

    Returns:
        Number of alerts sent.
    """
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.debug("[Alerts] Bot token or chat ID not set, skipping alerts")
        return 0

    keywords = _get_keywords()
    if not keywords:
        logger.debug("[Alerts] No keywords configured, skipping")
        return 0

    # Filter out competitor ads (info) — only alert on tenders and customer requests
    ALERT_TYPES = ("tender", "customer_request")
    relevant = [t for t in new_tenders if t.message_type in ALERT_TYPES]
    info_count = len(new_tenders) - len(relevant)
    if info_count:
        logger.info("[Alerts] Skipped %d info/ads (not tender or customer_request)", info_count)

    # Filter out tenders below minimum price (10M UZS)
    MIN_PRICE = 10_000_000
    priced = [t for t in relevant if t.price is None or t.price >= MIN_PRICE]
    low_price_count = len(new_tenders) - len(priced)
    if low_price_count:
        logger.info("[Alerts] Skipped %d tenders below %dM price threshold", low_price_count, MIN_PRICE // 1_000_000)

    # Filter out tenders with expired deadlines
    active = [t for t in priced if not _is_deadline_expired(t)]
    expired_count = len(new_tenders) - len(active)
    if expired_count:
        logger.info("[Alerts] Skipped %d tenders with expired deadlines", expired_count)

    # Filter matching tenders by keywords
    matching = []  # type: List[Tuple[RawTender, str]]
    for t in active:
        kw = _find_matching_keyword(t, keywords)
        if kw:
            matching.append((t, kw))

    if not matching:
        logger.info("[Alerts] No tenders match alert keywords (%d checked)", len(new_tenders))
        return 0

    logger.info("[Alerts] %d tenders match keywords (out of %d new)", len(matching), len(new_tenders))

    if dry_run:
        for t, kw in matching:
            logger.info("[Alerts] DRY RUN would send: [%s] %s", kw, t.title[:80])
        return len(matching)

    # AI relevance filter — reject false positives via Qwen
    if settings.openrouter_api_key:
        filtered = []  # type: List[Tuple[RawTender, str]]
        async with httpx.AsyncClient(timeout=15) as ai_client:
            for tender, kw in matching:
                if await _ai_check_relevance(tender, ai_client):
                    filtered.append((tender, kw))
        rejected = len(matching) - len(filtered)
        if rejected:
            logger.info("[AI Filter] Passed %d / %d (rejected %d)", len(filtered), len(matching), rejected)
        matching = filtered
        if not matching:
            logger.info("[Alerts] All tenders rejected by AI filter")
            return 0

    # Send via Telegram Bot API (no Telethon needed — just HTTP)
    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    sent = 0

    _group_sources = group_sources or {}

    async with httpx.AsyncClient(timeout=10) as client:
        for tender, kw in matching:
            extra = _group_sources.get(tender.id)
            text = _format_alert(tender, kw, extra_sources=extra)
            try:
                resp = await client.post(bot_url, json={
                    "chat_id": settings.telegram_alert_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                    "protect_content": True,
                })
                if resp.status_code == 200:
                    sent += 1
                else:
                    logger.warning(
                        "[Alerts] Failed to send alert: %d %s",
                        resp.status_code,
                        resp.text[:200],
                    )
            except Exception as exc:
                logger.warning("[Alerts] Error sending alert: %s", str(exc))

    logger.info("[Alerts] Sent %d / %d alerts", sent, len(matching))
    return sent


async def send_healthcheck(
    stats: dict,
    new_count: int,
    alerts_sent: int,
    errors: List[str] = None,
) -> None:
    """Send crawl summary to Telegram (silent, no notification sound).

    Only sends if there are errors or new tenders. Silent crawls with
    0 new tenders don't generate noise.
    """
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return

    total = sum(stats.values())
    sources_ok = sum(1 for v in stats.values() if v > 0)
    sources_fail = sum(1 for v in stats.values() if v == 0)

    # Skip quiet runs — no need to spam
    if not errors and new_count == 0:
        return

    parts = []  # type: List[str]
    parts.append("Crawl: %d тендеров (%d источников)" % (total, sources_ok))
    if new_count:
        parts.append("Новых: %d" % new_count)
    if alerts_sent:
        parts.append("Алертов: %d" % alerts_sent)
    if sources_fail:
        failed = [k for k, v in stats.items() if v == 0]
        parts.append("0 items: %s" % ", ".join(failed[:5]))
    if errors:
        parts.append("Ошибки: %s" % "; ".join(errors[:3]))

    text = "\n".join(parts)

    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "disable_notification": True,
                "protect_content": True,
            })
    except Exception as exc:
        logger.warning("[Healthcheck] Failed to send: %s", str(exc)[:80])
