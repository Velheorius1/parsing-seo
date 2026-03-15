"""Seasonal tender predictor — finds patterns by organization + month.

Algorithm:
1. Query tenders grouped by organization + month
2. Orgs with 3+ tenders in a month = seasonal pattern
3. If next month matches pattern -> store prediction + send Telegram alert
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)


def _get_client():  # type: ignore[no-untyped-def]
    """Lazy-init Supabase client."""
    from supabase import create_client

    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def _fetch_org_month_patterns() -> List[Dict]:
    """Query tenders grouped by organization + month, find patterns (3+ tenders)."""
    try:
        client = _get_client()
        # Fetch all tenders with organization and collected_at
        resp = client.table("tenders").select(
            "organization, collected_at"
        ).not_.is_("organization", "null").execute()

        if not resp.data:
            return []

        # Group by organization + month
        org_months = {}  # type: Dict[Tuple[str, int], int]
        org_products = {}  # type: Dict[str, List[str]]

        for row in resp.data:
            org = row.get("organization", "")
            if not org or len(org) < 3:
                continue
            collected = row.get("collected_at", "")
            if not collected:
                continue
            try:
                dt = datetime.fromisoformat(collected.replace("Z", "+00:00"))
                key = (org, dt.month)
                org_months[key] = org_months.get(key, 0) + 1
            except (ValueError, AttributeError):
                continue

        # Filter: only orgs with 3+ tenders in a given month
        patterns = []  # type: List[Dict]
        for (org, month), count in org_months.items():
            if count >= 3:
                patterns.append({
                    "organization": org,
                    "month": month,
                    "count": count,
                })

        return patterns

    except Exception as exc:
        logger.warning("[Predictor] Failed to fetch patterns: %s", str(exc)[:80])
        return []


def _compute_predictions(
    patterns: List[Dict],
    current_month: int,
    current_year: int,
) -> List[Dict]:
    """Compute predictions: if next month matches a pattern, predict it."""
    next_month = current_month + 1 if current_month < 12 else 1
    next_year = current_year if current_month < 12 else current_year + 1

    # Also check month after next
    month_after = next_month + 1 if next_month < 12 else 1
    year_after = next_year if next_month < 12 else next_year + 1

    predictions = []  # type: List[Dict]
    seen = set()  # type: set

    for p in patterns:
        org = p["organization"]
        month = p["month"]
        count = p["count"]

        # Confidence: more tenders = higher confidence
        confidence = min(round(count / 10.0, 2), 1.0)
        if confidence < 0.3:
            confidence = 0.3

        if month == next_month:
            key = (org, next_month, next_year)
            if key not in seen:
                seen.add(key)
                predictions.append({
                    "organization": org,
                    "predicted_month": next_month,
                    "predicted_year": next_year,
                    "confidence": confidence,
                    "basis": "%d тендеров в месяце %d ранее" % (count, month),
                    "product_hint": "",
                })
        elif month == month_after:
            key = (org, month_after, year_after)
            if key not in seen:
                seen.add(key)
                predictions.append({
                    "organization": org,
                    "predicted_month": month_after,
                    "predicted_year": year_after,
                    "confidence": max(confidence - 0.1, 0.2),
                    "basis": "%d тендеров в месяце %d ранее" % (count, month),
                    "product_hint": "",
                })

    return predictions


async def _store_predictions(predictions: List[Dict]) -> int:
    """Store predictions in Supabase, skip duplicates via UNIQUE constraint."""
    if not predictions:
        return 0

    try:
        client = _get_client()
        stored = 0

        for pred in predictions:
            try:
                client.table("tender_predictions").upsert(
                    pred,
                    on_conflict="organization,predicted_month,predicted_year",
                ).execute()
                stored += 1
            except Exception as exc:
                logger.debug("[Predictor] Upsert skipped: %s", str(exc)[:60])

        return stored

    except Exception as exc:
        logger.warning("[Predictor] Failed to store predictions: %s", str(exc)[:80])
        return 0


async def _send_prediction_alerts(predictions: List[Dict]) -> int:
    """Send Telegram alerts for new predictions (not yet notified)."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return 0

    month_names = {
        1: "январь", 2: "февраль", 3: "март", 4: "апрель",
        5: "май", 6: "июнь", 7: "июль", 8: "август",
        9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
    }

    # Only alert predictions not yet notified
    try:
        client = _get_client()
        resp = client.table("tender_predictions").select("*").eq(
            "notified", False
        ).execute()

        if not resp.data:
            return 0

        rows = resp.data
    except Exception as exc:
        logger.warning("[Predictor] Failed to fetch unnotified: %s", str(exc)[:80])
        return 0

    if not rows:
        return 0

    # Format message
    parts = []  # type: List[str]
    parts.append("*Прогноз тендеров*\n")

    for row in rows[:10]:  # max 10 per message
        month_name = month_names.get(row["predicted_month"], str(row["predicted_month"]))
        conf_pct = int(row["confidence"] * 100)
        org = row["organization"][:60]
        # Escape markdown
        org = org.replace("*", "").replace("_", "").replace("`", "")
        parts.append(
            "- %s: ожидается в %s %d (%d%%)"
            % (org, month_name, row["predicted_year"], conf_pct)
        )

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
                # Mark as notified
                ids = [r["id"] for r in rows]
                for rid in ids:
                    try:
                        client.table("tender_predictions").update(
                            {"notified": True}
                        ).eq("id", rid).execute()
                    except Exception:
                        pass
            else:
                logger.warning(
                    "[Predictor] Telegram send failed: %d",
                    resp_tg.status_code,
                )
    except Exception as exc:
        logger.warning("[Predictor] Telegram error: %s", str(exc)[:80])

    return sent


async def run_predictions(dry_run: bool = False) -> int:
    """Main entry: find patterns, compute predictions, store & alert.

    Returns number of new predictions stored.
    """
    now = datetime.utcnow()

    patterns = await _fetch_org_month_patterns()
    if not patterns:
        logger.debug("[Predictor] No seasonal patterns found")
        return 0

    logger.info("[Predictor] Found %d seasonal patterns", len(patterns))

    predictions = _compute_predictions(patterns, now.month, now.year)
    if not predictions:
        logger.debug("[Predictor] No predictions for upcoming months")
        return 0

    logger.info("[Predictor] %d predictions computed", len(predictions))

    if dry_run:
        for p in predictions:
            logger.info(
                "[Predictor] DRY RUN: %s -> month %d/%d (%.0f%%)",
                p["organization"][:40],
                p["predicted_month"],
                p["predicted_year"],
                p["confidence"] * 100,
            )
        return len(predictions)

    stored = await _store_predictions(predictions)
    if stored:
        logger.info("[Predictor] Stored %d new predictions", stored)

    sent = await _send_prediction_alerts(predictions)
    if sent:
        logger.info("[Predictor] Sent %d prediction alerts", sent)

    return stored
