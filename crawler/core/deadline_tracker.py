"""Deadline tracker — sends reminders for tenders with approaching deadlines.

Checks DB for active tenders with deadlines in 3 days and 1 day.
Tracks sent reminders to avoid duplicates.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# Last-known-good snapshot of active tenders with deadlines, so a transient DB timeout
# (57014 under crawl load) doesn't skip ALL reminders for a run. Safe to serve stale:
# deadlines are absolute dates (re-filtered by the current window every run) and the
# deadline_reminders table dedups sends, so a stale row can never double-remind.
_DEADLINE_CACHE_FILE = "/opt/parsing-seo/logs/deadline_active_cache.json"
_DEADLINE_SELECT = "id,title,organization,price,currency,deadline,source,source_url"
# >this many reminders in one window → send ONE grouped digest instead of N pushes.
# Prevents a burst (feature revival backlog, or a Monday pile-up) from blasting the chat.
_DIGEST_THRESHOLD = 6


def _save_deadline_cache(rows):
    # type: (list) -> None
    import json
    import os
    try:
        os.makedirs(os.path.dirname(_DEADLINE_CACHE_FILE), exist_ok=True)
        with open(_DEADLINE_CACHE_FILE, "w") as f:
            json.dump(rows, f, ensure_ascii=False)
    except Exception as exc:
        logger.debug("[Deadlines] cache save failed: %s", str(exc)[:80])


def _load_deadline_cache():
    # type: () -> list
    import json
    try:
        with open(_DEADLINE_CACHE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _fetch_active_deadline_tenders(client):
    # type: (object) -> list
    """All active, keyword-matched tenders that carry a deadline. Fetched ONCE per run
    (the old code re-ran this identical query per reminder window = 2x the load that
    triggered 57014). Retries the transient timeout via query_with_retry; on total
    failure falls back to the last-known-good snapshot so reminders still fire from
    slightly-stale data instead of being silently skipped."""
    from crawler.core.db import query_with_retry

    # Relevance gate = alert_seq (we alerted on it → Daniyar saw & cares). The old
    # matched_keywords gate was empty on all 540k rows (never populated) — it both
    # silenced the feature entirely AND forced a full scan of ~487k active-with-deadline
    # rows, which is what triggered 57014. alert_seq is indexed & tiny (~2k rows).
    # MUST paginate: PostgREST caps at 1000/response and the upcoming ISO deadlines sit
    # past row 1000, so a single .execute() returned them empty (dry-run showed 0).
    rows = []  # type: list
    offset = 0
    while True:
        def _q(o=offset):
            return (client.table("tenders").select(_DEADLINE_SELECT)
                    .eq("status", "active").not_.is_("deadline", "null")
                    .not_.is_("alert_seq", "null").range(o, o + 999).execute())
        try:
            resp = query_with_retry(_q, label="deadline tenders p%d" % offset)
        except Exception as exc:
            if rows:
                logger.warning("[Deadlines] page %d failed (%s) — using %d rows so far",
                               offset, str(exc)[:60], len(rows))
                break
            cached = _load_deadline_cache()
            logger.error("[Deadlines] query failed after retries (%s) — using %d cached rows (NOT skipping)",
                         str(exc)[:80], len(cached))
            return cached
        page = resp.data or []
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
        if offset > 20000:  # safety cap (alerted-active-with-deadline is ~2k)
            logger.warning("[Deadlines] pagination hit 20k cap")
            break
    _save_deadline_cache(rows)
    return rows


def _parse_deadline(deadline_str: Optional[str]) -> Optional[datetime]:
    """Parse deadline string into datetime.

    Some sources store free text like "Опубликовано: 28/04/2026 ... Истекает: 13/05/2026"
    where the FIRST date is the publish date, not the deadline. When an expiry label is
    present, parse only the tail after it so we don't remind on the publish date."""
    if not deadline_str:
        return None
    m_label = re.search(r"(?:Истека[а-я]*|Дедлайн|[Сс]рок)\D*(\d.*)$", deadline_str)
    search_str = m_label.group(1) if m_label else deadline_str
    patterns = [
        (r"(\d{4})-(\d{2})-(\d{2})", "%Y-%m-%d"),
        (r"(\d{2})\.(\d{2})\.(\d{4})", "%d.%m.%Y"),
        (r"(\d{2})/(\d{2})/(\d{4})", "%d/%m/%Y"),
    ]
    for pattern, fmt in patterns:
        m = re.search(pattern, search_str)
        if m:
            try:
                return datetime.strptime(m.group(0), fmt)
            except ValueError:
                continue
    return None


_DETAIL_PAGE_BASE = "https://parsing-seo.vercel.app/tenders"


def _reminder_url(t):
    # type: (dict) -> Optional[str]
    """Ссылка в напоминании о дедлайне.

    Напоминания были ЕДИНСТВЕННЫМ каналом, куда не дошла link-стратегия пушей:
    слали голый source_url. Скриншот Данияра (21:09, `supplier/lots?lotId=…`) —
    именно отсюда. Проверено кликами 24.08: у Cooperation этот маршрут отдаёт
    404 даже на десктопе (площадка обновилась после записи от 05.08 про
    «инертный параметр»), а на телефоне ЛЮБАЯ coop-ссылка редиректится на
    заглушку мобильного приложения (mobile.cooperation.uz) — чистый iPhone-UA
    через резидентный прокси, обе ссылки. Поэтому для битых SPA и всего
    Cooperation — наш архив; _DEADLINE_SELECT уже несёт `id` (uuid карточки).
    """
    try:
        from crawler.core.snap import is_broken_spa
        dead = is_broken_spa(t.get("source") or "")
    except Exception:
        dead = False
    if (dead or (t.get("source") or "").startswith("Cooperation.uz")) and t.get("id"):
        return "%s/%s" % (_DETAIL_PAGE_BASE, t["id"])
    return t.get("source_url")


def _format_reminder(tender: dict, reminder_type: str) -> str:
    """Format a deadline reminder message."""
    emoji = "⏰" if reminder_type == "1_day" else "📅"
    days_text = "ЗАВТРА" if reminder_type == "1_day" else "через 3 дня"

    parts = []
    parts.append("%s *Дедлайн %s!*" % (emoji, days_text))
    parts.append("")
    title = (tender.get("title") or "")[:200]
    # Escape markdown
    for ch in ("*", "_", "`", "["):
        title = title.replace(ch, "")
    parts.append(title)
    org = tender.get("organization")
    if org:
        parts.append("Заказчик: %s" % org)
    price = tender.get("price")
    if price:
        currency = tender.get("currency", "UZS")
        parts.append("Сумма: %s %s" % ("{:,.0f}".format(float(price)), currency))
    parts.append("Дедлайн: %s" % tender.get("deadline", "?"))
    parts.append("Источник: %s" % tender.get("source", "?"))
    url = _reminder_url(tender)
    if url:
        parts.append(url)
    return "\n".join(parts)


def _format_digest(tenders: List[dict], reminder_type: str) -> str:
    """One grouped message for a burst of same-window reminders, so a pile-up (feature
    revival backlog, or a Monday cluster) never blasts N separate pushes. Caps the list
    to stay under Telegram's 4096-char limit; all are still recorded as sent."""
    emoji = "⏰" if reminder_type == "1_day" else "📅"
    days_text = "ЗАВТРА" if reminder_type == "1_day" else "через 3 дня"
    parts = ["%s *Дедлайн %s — %d тендеров:*" % (emoji, days_text, len(tenders)), ""]
    shown = tenders[:25]
    for t in shown:
        title = (t.get("title") or "")[:80]
        for ch in ("*", "_", "`", "["):
            title = title.replace(ch, "")
        url = _reminder_url(t)
        parts.append("• %s%s" % (title, ("\n  " + url) if url else ""))
    if len(tenders) > len(shown):
        parts.append("…и ещё %d" % (len(tenders) - len(shown)))
    return "\n".join(parts)


async def check_deadlines(dry_run: bool = False) -> int:
    """Check for tenders with approaching deadlines and send reminders.

    Returns number of reminders sent.
    """
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.debug("[Deadlines] Bot token or chat ID not set, skipping")
        return 0

    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.debug("[Deadlines] Supabase not configured, skipping")
        return 0

    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sent = 0

    # Fetch the active-with-deadline set ONCE (was re-queried per window = double the
    # load that caused 57014). Resilient: retries + stale-cache fallback.
    active_tenders = _fetch_active_deadline_tenders(client)
    if not active_tenders:
        logger.debug("[Deadlines] no active tenders with deadlines")
        return 0

    for reminder_type, days_ahead in [("3_days", 3), ("1_day", 1)]:
        # Find active tenders with deadline in `days_ahead` days (±12 hours window)
        target_date = now + timedelta(days=days_ahead)
        window_start = target_date - timedelta(hours=12)
        window_end = target_date + timedelta(hours=12)

        # Filter by deadline date (parse and check range)
        candidates = []  # type: List[dict]
        for tender in active_tenders:
            dt = _parse_deadline(tender.get("deadline"))
            if dt and window_start <= dt <= window_end:
                candidates.append(tender)

        if not candidates:
            logger.debug("[Deadlines] No tenders with deadline in %d days", days_ahead)
            continue

        # Check which reminders already sent
        tender_ids = [t["id"] for t in candidates]
        try:
            existing = (
                client.table("deadline_reminders")
                .select("tender_id")
                .eq("reminder_type", reminder_type)
                .in_("tender_id", tender_ids)
                .execute()
            )
            already_sent = {r["tender_id"] for r in (existing.data or [])}
        except Exception as exc:
            logger.warning("[Deadlines] Failed to check existing reminders: %s", str(exc)[:80])
            already_sent = set()

        # Filter out already reminded
        to_remind = [t for t in candidates if t["id"] not in already_sent]

        if not to_remind:
            logger.debug(
                "[Deadlines] All %d tenders with %s deadline already reminded",
                len(candidates), reminder_type,
            )
            continue

        logger.info(
            "[Deadlines] %d tenders need %s reminder (out of %d candidates)",
            len(to_remind), reminder_type, len(candidates),
        )

        if dry_run:
            for t in to_remind:
                logger.info("[Deadlines] DRY RUN: would remind: %s", t["title"][:60])
            sent += len(to_remind)
            continue

        # Send reminders
        bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token

        def _record(tender_ids):
            for tid in tender_ids:
                try:
                    client.table("deadline_reminders").insert({
                        "tender_id": tid, "reminder_type": reminder_type}).execute()
                except Exception as exc:
                    logger.warning("[Deadlines] Failed to record reminder: %s", str(exc)[:80])

        async with httpx.AsyncClient(timeout=10) as http_client:
            if len(to_remind) > _DIGEST_THRESHOLD:
                # Burst → one grouped digest (never blast N pushes).
                text = _format_digest(to_remind, reminder_type)
                try:
                    resp_tg = await http_client.post(bot_url, json={
                        "chat_id": settings.telegram_alert_chat_id,
                        "text": text, "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    })
                    if resp_tg.status_code == 200:
                        _record([t["id"] for t in to_remind])
                        sent += len(to_remind)
                        logger.info("[Deadlines] Sent digest of %d %s reminders",
                                    len(to_remind), reminder_type)
                    else:
                        logger.warning("[Deadlines] Digest send failed: %d %s",
                                       resp_tg.status_code, resp_tg.text[:100])
                except Exception as exc:
                    logger.warning("[Deadlines] Digest send error: %s", str(exc)[:80])
            else:
                for tender in to_remind:
                    text = _format_reminder(tender, reminder_type)
                    try:
                        resp_tg = await http_client.post(bot_url, json={
                            "chat_id": settings.telegram_alert_chat_id,
                            "text": text, "parse_mode": "Markdown",
                            "disable_web_page_preview": True,
                        })
                        if resp_tg.status_code == 200:
                            _record([tender["id"]])
                            sent += 1
                        else:
                            logger.warning("[Deadlines] Telegram send failed: %d %s",
                                           resp_tg.status_code, resp_tg.text[:100])
                    except Exception as exc:
                        logger.warning("[Deadlines] Send error: %s", str(exc)[:80])

    if sent:
        logger.info("[Deadlines] Sent %d deadline reminders", sent)
    return sent
