"""Deadline tracker — sends reminders for tenders with approaching deadlines.

Checks DB for active tenders with deadlines in 3 days and 1 day.
Tracks sent reminders to avoid duplicates.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)


def _parse_deadline(deadline_str: Optional[str]) -> Optional[datetime]:
    """Parse deadline string into datetime."""
    if not deadline_str:
        return None
    patterns = [
        (r"(\d{4})-(\d{2})-(\d{2})", "%Y-%m-%d"),
        (r"(\d{2})\.(\d{2})\.(\d{4})", "%d.%m.%Y"),
        (r"(\d{2})/(\d{2})/(\d{4})", "%d/%m/%Y"),
    ]
    for pattern, fmt in patterns:
        m = re.search(pattern, deadline_str)
        if m:
            try:
                return datetime.strptime(m.group(0), fmt)
            except ValueError:
                continue
    return None


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
    url = tender.get("source_url")
    if url:
        parts.append(url)
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

    now = datetime.utcnow()
    sent = 0

    for reminder_type, days_ahead in [("3_days", 3), ("1_day", 1)]:
        # Find active tenders with deadline in `days_ahead` days (±12 hours window)
        target_date = now + timedelta(days=days_ahead)
        window_start = target_date - timedelta(hours=12)
        window_end = target_date + timedelta(hours=12)

        # Get active tenders that matched our keywords (not all tenders!)
        try:
            resp = (
                client.table("tenders")
                .select("id,title,organization,price,currency,deadline,source,source_url,matched_keywords")
                .eq("status", "active")
                .not_.is_("deadline", "null")
                .not_.eq("matched_keywords", "{}")
                .execute()
            )
        except Exception as exc:
            logger.error("[Deadlines] Failed to query tenders: %s", str(exc)[:80])
            continue

        if not resp.data:
            continue

        # Filter by deadline date (parse and check range)
        candidates = []  # type: List[dict]
        for tender in resp.data:
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

        async with httpx.AsyncClient(timeout=10) as http_client:
            for tender in to_remind:
                text = _format_reminder(tender, reminder_type)
                try:
                    resp_tg = await http_client.post(bot_url, json={
                        "chat_id": settings.telegram_alert_chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    })
                    if resp_tg.status_code == 200:
                        # Record that we sent this reminder
                        try:
                            client.table("deadline_reminders").insert({
                                "tender_id": tender["id"],
                                "reminder_type": reminder_type,
                            }).execute()
                        except Exception as exc:
                            logger.warning(
                                "[Deadlines] Failed to record reminder: %s",
                                str(exc)[:80],
                            )
                        sent += 1
                    else:
                        logger.warning(
                            "[Deadlines] Telegram send failed: %d %s",
                            resp_tg.status_code, resp_tg.text[:100],
                        )
                except Exception as exc:
                    logger.warning("[Deadlines] Send error: %s", str(exc)[:80])

    if sent:
        logger.info("[Deadlines] Sent %d deadline reminders", sent)
    return sent
