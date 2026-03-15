"""Gold standard: Prediction/analytics module pattern (Supabase + Telegram).

Pattern for modules that query Supabase, compute analytics/predictions,
store results back, and send Telegram alerts.
Used by: predictor.py. Follow this for any new analytics module.

Key conventions:
- Lazy Supabase client init (avoid import-time side effects)
- Upsert with on_conflict for idempotent writes
- Async entry point with dry_run support
- Telegram alerts for new results only (track notified flag)
- All typing via typing module (Python 3.9 compat)
- %-format strings, not f-strings (consistency with codebase)
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)


# --- Lazy Supabase client ---

def _get_client():  # type: ignore[no-untyped-def]
    """Lazy-init Supabase client. Import at call time, not module level."""
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# --- Data fetching pattern ---

async def _fetch_data() -> List[Dict]:
    """Query Supabase for raw data. Returns [] on error (never raises)."""
    try:
        client = _get_client()
        resp = client.table("tenders").select(
            "organization, collected_at, price"
        ).not_.is_("organization", "null").execute()

        if not resp.data:
            return []

        return resp.data

    except Exception as exc:
        logger.warning("[Analytics] Failed to fetch data: %s", str(exc)[:80])
        return []


# --- Computation pattern ---

def _compute_results(data, current_date):
    # type: (List[Dict], datetime) -> List[Dict]
    """Pure computation: data in -> results out. No I/O here."""
    results = []  # type: List[Dict]

    # Group, aggregate, compute patterns...
    # Return list of dicts ready for Supabase upsert

    return results


# --- Storage pattern (idempotent upsert) ---

async def _store_results(results):
    # type: (List[Dict]) -> int
    """Store results via upsert (on_conflict). Returns count stored."""
    if not results:
        return 0

    try:
        client = _get_client()
        stored = 0

        for row in results:
            try:
                client.table("analytics_results").upsert(
                    row,
                    on_conflict="unique_key_column",  # UNIQUE constraint
                ).execute()
                stored += 1
            except Exception as exc:
                logger.debug("[Analytics] Upsert skipped: %s", str(exc)[:60])

        return stored

    except Exception as exc:
        logger.warning("[Analytics] Failed to store: %s", str(exc)[:80])
        return 0


# --- Telegram alert pattern (only unnotified) ---

async def _send_alerts(results):
    # type: (List[Dict]) -> int
    """Send Telegram alerts for new (unnotified) results."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return 0

    try:
        client = _get_client()
        resp = client.table("analytics_results").select("*").eq(
            "notified", False
        ).execute()

        if not resp.data:
            return 0

        rows = resp.data
    except Exception as exc:
        logger.warning("[Analytics] Failed to fetch unnotified: %s", str(exc)[:80])
        return 0

    # Format message
    parts = []  # type: List[str]
    parts.append("*Analytics Report*\n")
    for row in rows[:10]:  # max 10 per message
        # Escape markdown in user content
        label = row.get("label", "")[:60]
        label = label.replace("*", "").replace("_", "").replace("`", "")
        parts.append("- %s" % label)

    text = "\n".join(parts)

    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    sent = 0
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            resp_tg = await http_client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_notification": True,
            })
            if resp_tg.status_code == 200:
                sent = len(rows)
                # Mark notified
                for r in rows:
                    try:
                        client.table("analytics_results").update(
                            {"notified": True}
                        ).eq("id", r["id"]).execute()
                    except Exception:
                        pass
    except Exception as exc:
        logger.warning("[Analytics] Telegram error: %s", str(exc)[:80])

    return sent


# --- Main entry point ---

async def run_analytics(dry_run=False):
    # type: (bool) -> int
    """Main entry: fetch -> compute -> store -> alert.

    Returns number of results stored. Supports dry_run.
    """
    data = await _fetch_data()
    if not data:
        logger.debug("[Analytics] No data to analyze")
        return 0

    logger.info("[Analytics] Fetched %d rows", len(data))

    results = _compute_results(data, datetime.utcnow())
    if not results:
        logger.debug("[Analytics] No results computed")
        return 0

    logger.info("[Analytics] %d results computed", len(results))

    if dry_run:
        for r in results:
            logger.info("[Analytics] DRY RUN: %s", str(r)[:100])
        return len(results)

    stored = await _store_results(results)
    if stored:
        logger.info("[Analytics] Stored %d results", stored)

    sent = await _send_alerts(results)
    if sent:
        logger.info("[Analytics] Sent %d alerts", sent)

    return stored
