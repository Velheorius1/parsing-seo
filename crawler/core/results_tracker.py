"""Results tracker — monitors completed tenders for winners and prices.

Checks UZEX API for completed trades and updates tender records.
Sends Telegram alerts for interesting results (competitors, our niche).
"""

import logging
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# UZEX API endpoints for results
_UZEX_RESULTS_URL = "https://apietender.uzex.uz/api/Common/TradeHistory"
_UZEX_RESULT_DETAIL = "https://apietender.uzex.uz/api/Common/TradeResult"


async def _fetch_uzex_results(
    client: httpx.AsyncClient,
    limit: int = 100,
) -> List[dict]:
    """Fetch completed trades from UZEX API."""
    try:
        resp = await client.post(
            _UZEX_RESULTS_URL,
            json={"from": 0, "to": limit},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("[Results] UZEX API %d: %s", resp.status_code, resp.text[:100])
            return []

        data = resp.json()
        # UZEX returns list of trade items or wrapped in response
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Try common response patterns
            for key in ("data", "items", "trades", "result"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            # Try indexed access (UZEX pattern: {0: {items: [...]}, total_count: N})
            if "0" in data:
                inner = data["0"]
                if isinstance(inner, dict):
                    for key in ("items", "data", "trades"):
                        if key in inner:
                            return inner[key]
                    # Maybe the inner dict itself has a list
                    if "total_count" in data:
                        items = [v for k, v in inner.items() if isinstance(v, dict)]
                        if items:
                            return items
        return []
    except Exception as exc:
        logger.warning("[Results] UZEX fetch error: %s", str(exc)[:80])
        return []


def _extract_winner_info(item: dict) -> Optional[Dict[str, str]]:
    """Extract winner info from a UZEX trade result item."""
    # Common field names for winner across UZEX APIs
    winner_fields = ["winner_name", "buyer_name", "winner", "supplier_name"]
    price_fields = ["final_cost", "result_cost", "winning_price", "cost"]
    id_fields = ["display_no", "trade_no", "id"]

    winner = None
    for f in winner_fields:
        if f in item and item[f]:
            winner = str(item[f]).strip()
            break

    price = None
    for f in price_fields:
        if f in item and item[f]:
            try:
                price = float(item[f])
            except (ValueError, TypeError):
                pass
            break

    ext_id = None
    for f in id_fields:
        if f in item and item[f]:
            ext_id = str(item[f]).strip()
            break

    if not ext_id:
        return None

    result = {"external_id": ext_id}
    if winner:
        result["winner"] = winner
    if price is not None:
        result["winning_price"] = str(price)
    return result


async def update_results(dry_run: bool = False) -> int:
    """Fetch completed tender results and update DB records.

    Returns number of tenders updated with result info.
    """
    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.debug("[Results] Supabase not configured, skipping")
        return 0

    from supabase import create_client
    db = create_client(settings.supabase_url, settings.supabase_service_role_key)

    updated = 0

    async with httpx.AsyncClient(timeout=15) as client:
        # Fetch UZEX results
        results = await _fetch_uzex_results(client)
        if not results:
            logger.info("[Results] No UZEX results found (API may not be available)")
            return 0

        logger.info("[Results] Fetched %d UZEX result records", len(results))

        for item in results:
            info = _extract_winner_info(item)
            if not info:
                continue

            ext_id = info["external_id"]
            update_data = {"status": "completed"}
            if "winner" in info:
                update_data["winner"] = info["winner"]
            if "winning_price" in info:
                update_data["winning_price"] = float(info["winning_price"])

            if dry_run:
                logger.info(
                    "[Results] DRY RUN: would update etender-%s: winner=%s",
                    ext_id, info.get("winner", "?"),
                )
                updated += 1
                continue

            # Update tenders matching this external_id from etender source
            try:
                resp = (
                    db.table("tenders")
                    .update(update_data)
                    .eq("external_id", "etender-%s" % ext_id)
                    .execute()
                )
                if resp.data:
                    updated += 1
            except Exception as exc:
                logger.warning("[Results] DB update failed for %s: %s", ext_id, str(exc)[:80])

    if updated:
        logger.info("[Results] Updated %d tenders with result data", updated)

    # Send alert for results in our niche
    if updated and not dry_run:
        await _alert_interesting_results(db)

    return updated


async def _alert_interesting_results(db) -> None:
    """Send Telegram alert for recently completed tenders in our niche."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return

    try:
        # Get recently completed tenders with winners
        resp = (
            db.table("tenders")
            .select("title,organization,winner,winning_price,currency,source_url")
            .eq("status", "completed")
            .not_.is_("winner", "null")
            .order("updated_at", desc=True)
            .limit(5)
            .execute()
        )
        if not resp.data:
            return

        parts = ["*Результаты тендеров:*", ""]
        for t in resp.data:
            title = (t.get("title") or "")[:100]
            for ch in ("*", "_", "`", "["):
                title = title.replace(ch, "")
            winner = t.get("winner", "?")
            price = t.get("winning_price")
            line = "• %s" % title
            if winner:
                line += "\n  Победитель: %s" % winner
            if price:
                line += " | %s %s" % ("{:,.0f}".format(float(price)), t.get("currency", "UZS"))
            parts.append(line)

        text = "\n".join(parts)
        bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
                "disable_notification": True,
            })
    except Exception as exc:
        logger.warning("[Results] Alert send failed: %s", str(exc)[:80])
