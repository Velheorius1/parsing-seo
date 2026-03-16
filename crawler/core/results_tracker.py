"""Results tracker — monitors completed tenders for winners and prices.

Uses UZEX CivilContracts/GetResulted API (public, 5000+ results).
Updates tender records with winner info and sends Telegram alerts.
"""

import logging
from typing import Dict, List, Optional

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# UZEX CivilContracts API — public endpoint for completed deals
_UZEX_RESULTS_URL = "https://apietender.uzex.uz/api/CivilContracts/GetResulted"
_UZEX_NOT_RESULTED_URL = "https://apietender.uzex.uz/api/CivilContracts/GetNotResulted"


async def _fetch_uzex_results(
    client: httpx.AsyncClient,
    limit: int = 200,
) -> List[dict]:
    """Fetch completed deals from UZEX CivilContracts API.

    Returns list of deal dicts with fields:
    - civil_name: subject
    - customer_name: buyer org
    - customer_inn: buyer INN
    - provider_inn: winner INN
    - provider_name: winner name (sometimes null)
    - provider_address: winner address
    - cost: starting price
    - result_cost: final deal price
    - deal_num: contract number
    - deal_date: contract date
    - status_name: "Сделка совершена" / "Несостоявшийся"
    - display_id: public lot number
    """
    try:
        resp = await client.post(
            _UZEX_RESULTS_URL,
            json={"from": 0, "to": limit},
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        if resp.status_code != 200:
            logger.warning("[Results] UZEX API %d: %s", resp.status_code, resp.text[:100])
            return []

        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # UZEX wraps in {0: [...], total_count: N} or {data: [...]}
            for key in ("data", "items", "result"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            # Indexed pattern
            if "0" in data and isinstance(data["0"], list):
                return data["0"]
        return []
    except Exception as exc:
        logger.warning("[Results] UZEX fetch error: %s", str(exc)[:80])
        return []


def _extract_winner_info(item: dict) -> Optional[Dict[str, str]]:
    """Extract winner info from a CivilContracts result item."""
    # display_id is the public lot number (e.g. "26120000010097")
    ext_id = item.get("display_id") or item.get("civil_contract_id")
    if not ext_id:
        return None
    ext_id = str(ext_id).strip()

    result = {"external_id": ext_id}

    # Winner: provider_name or fallback to provider_inn
    winner = item.get("provider_name")
    if not winner:
        inn = item.get("provider_inn")
        if inn:
            winner = "ИНН: %s" % inn
        addr = item.get("provider_address")
        if addr and not winner:
            winner = addr
    if winner:
        result["winner"] = str(winner).strip()

    # Price: result_cost (final deal price) > cost (starting price)
    price = item.get("result_cost") or item.get("cost")
    if price:
        try:
            result["winning_price"] = str(float(price))
        except (ValueError, TypeError):
            pass

    # Deal date
    deal_date = item.get("deal_date")
    if deal_date:
        result["result_date"] = str(deal_date)

    # Status
    status = item.get("status_name", "")
    result["status"] = "completed" if status.lower() == "сделка совершена" else "cancelled"

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

    async with httpx.AsyncClient(timeout=20) as client:
        # Fetch UZEX completed deals
        results = await _fetch_uzex_results(client)
        if not results:
            logger.info("[Results] No UZEX results fetched")
            return 0

        logger.info("[Results] Fetched %d UZEX deal results", len(results))

        for item in results:
            info = _extract_winner_info(item)
            if not info:
                continue

            ext_id = info["external_id"]
            update_data = {"status": info.get("status", "completed")}
            if "winner" in info:
                update_data["winner"] = info["winner"]
            if "winning_price" in info:
                update_data["winning_price"] = float(info["winning_price"])
            if "result_date" in info:
                update_data["result_date"] = info["result_date"]

            if dry_run:
                logger.info(
                    "[Results] DRY RUN: %s winner=%s price=%s",
                    ext_id, info.get("winner", "?")[:40], info.get("winning_price", "?"),
                )
                updated += 1
                continue

            # Try to match by display_id in external_id field
            # ETender external_id format: "etender-{display_no}"
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

    # Send alert for results in our niche (only if we actually updated something)
    if updated > 0 and not dry_run:
        await _alert_niche_results(db)

    return updated


async def _alert_niche_results(db) -> None:
    """Send Telegram alert for recently completed tenders in our niche.

    Only alerts for tenders that matched our keywords (have matched_keywords).
    """
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return

    try:
        # Get recently completed tenders that match our keywords
        resp = (
            db.table("tenders")
            .select("title,organization,winner,winning_price,currency,source_url,matched_keywords")
            .eq("status", "completed")
            .not_.is_("winner", "null")
            .order("updated_at", desc=True)
            .limit(10)
            .execute()
        )
        if not resp.data:
            return

        # Filter to only our niche (has matched keywords)
        niche = [t for t in resp.data if t.get("matched_keywords")]
        if not niche:
            return

        parts = ["*Результаты тендеров (наша ниша):*", ""]
        for t in niche[:5]:
            title = (t.get("title") or "")[:100]
            for ch in ("*", "_", "`", "["):
                title = title.replace(ch, "")
            winner = t.get("winner", "?")
            price = t.get("winning_price")
            line = "- %s" % title
            if winner:
                line += "\n  Победитель: %s" % winner
            if price:
                line += " | %s %s" % ("{:,.0f}".format(float(price)), t.get("currency", "UZS"))
            url = t.get("source_url")
            if url:
                line += "\n  %s" % url
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
            logger.info("[Results] Sent niche results alert (%d items)", len(niche[:5]))
    except Exception as exc:
        logger.warning("[Results] Alert send failed: %s", str(exc)[:80])
