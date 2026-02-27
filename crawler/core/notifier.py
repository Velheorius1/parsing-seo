"""Telegram alert notifier — sends new matching tenders to a Telegram chat."""

import logging
from typing import List, Optional, Tuple

import httpx

from crawler.config.settings import settings
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

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


def _format_alert(tender: RawTender, matched_kw: str) -> str:
    """Format a single tender alert message for Telegram."""
    parts = []
    parts.append("*%s*" % tender.title[:200].replace("*", ""))
    if tender.organization:
        parts.append("Заказчик: %s" % tender.organization)
    if tender.price:
        parts.append("Сумма: %s %s" % ("{:,.0f}".format(tender.price), tender.currency))
    if tender.deadline:
        parts.append("Дедлайн: %s" % tender.deadline)
    parts.append("Источник: %s" % tender.source)
    if tender.source_url:
        parts.append(tender.source_url)
    parts.append("#%s" % matched_kw.replace(" ", "_"))
    return "\n".join(parts)


async def send_alerts(
    new_tenders: List[RawTender],
    dry_run: bool = False,
) -> int:
    """Filter tenders by keywords and send Telegram alerts.

    Args:
        new_tenders: List of tenders that were newly inserted (not seen before).
        dry_run: If True, log but don't send.

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

    # Filter matching tenders
    matching = []  # type: List[Tuple[RawTender, str]]
    for t in new_tenders:
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

    # Send via Telegram Bot API (no Telethon needed — just HTTP)
    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    sent = 0

    async with httpx.AsyncClient(timeout=10) as client:
        for tender, kw in matching:
            text = _format_alert(tender, kw)
            try:
                resp = await client.post(bot_url, json={
                    "chat_id": settings.telegram_alert_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
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
