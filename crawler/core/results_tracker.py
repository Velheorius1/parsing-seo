"""Results tracker — monitors completed tenders for winners and prices.

Uses UZEX CivilContracts/GetResulted API (public, 5000+ results).
Upserts completed deals into tenders table with winner, price, discount.
Sends Telegram alerts for results in our niche.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# UZEX CivilContracts API — public endpoint for completed deals
_UZEX_RESULTS_URL = "https://apietender.uzex.uz/api/CivilContracts/GetResulted"

# Source name for upserted results
_RESULTS_SOURCE = "UZEX Результаты"

# crawler_settings key for niche results dedup state.
# Value shape: {"alerted_ids": ["result-123", ...]}  (list, kept sorted by recency).
_NICHE_ALERTED_STATE_KEY = "niche_results_alerted_state"

# Cap on alerted_ids list — FIFO eviction. UZEX returns last 500 deals;
# our niche is ~7-15 of those, so 1000 covers months of history.
_NICHE_ALERTED_CAP = 1000


async def _fetch_uzex_results(
    client: httpx.AsyncClient,
    limit: int = 500,
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
            for key in ("data", "items", "result"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            if "0" in data and isinstance(data["0"], list):
                return data["0"]
        return []
    except Exception as exc:
        logger.warning("[Results] UZEX fetch error: %s", str(exc)[:80])
        return []


def _calc_discount(start_price, final_price):
    # type: (Optional[float], Optional[float]) -> Optional[float]
    """Calculate discount percentage: (start - final) / start * 100."""
    if not start_price or not final_price or start_price <= 0:
        return None
    discount = (start_price - final_price) / start_price * 100.0
    return round(discount, 1)


def _build_result_row(item):
    # type: (dict) -> Optional[dict]
    """Build a tenders table row from a CivilContracts result item."""
    display_id = item.get("display_id") or item.get("civil_contract_id")
    if not display_id:
        return None

    ext_id = str(display_id).strip()
    title = (item.get("civil_name") or "").strip()
    if not title:
        return None

    # Winner: provider_name or fallback to provider_inn
    winner = item.get("provider_name")
    if not winner:
        inn = item.get("provider_inn")
        if inn:
            winner = "ИНН: %s" % inn
        else:
            addr = item.get("provider_address")
            if addr:
                winner = str(addr).strip()
    if winner:
        winner = str(winner).strip()

    # Prices
    start_price = None
    final_price = None
    try:
        cost = item.get("cost")
        if cost:
            start_price = float(cost)
    except (ValueError, TypeError):
        pass
    try:
        result_cost = item.get("result_cost")
        if result_cost:
            final_price = float(result_cost)
    except (ValueError, TypeError):
        pass

    discount = _calc_discount(start_price, final_price)

    # Status
    status_name = item.get("status_name", "")
    status = "completed" if "совершена" in status_name.lower() else "cancelled"

    # Customer
    customer = (item.get("customer_name") or "").strip()

    # Deal date
    deal_date = item.get("deal_date")
    result_date = str(deal_date) if deal_date else None

    # Currency
    currency_name = item.get("currency_name", "")
    currency = "UZS"
    if currency_name and "сом" not in currency_name.lower():
        currency = currency_name

    row = {
        "external_id": "result-%s" % ext_id,
        "title": title,
        "organization": customer,
        "price": start_price,
        "winning_price": final_price,
        "currency": currency,
        "winner": winner,
        "status": status,
        "result_date": result_date,
        "source": _RESULTS_SOURCE,
        "source_url": "https://etender.uzex.uz/lot/%s" % ext_id,
        "search_text": ("%s %s %s" % (title, customer, winner or "")).strip(),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "message_type": "result",
    }
    # Add discount as JSON metadata (no extra DB column needed)
    # We store it in the 'region' field as "discount:XX.X%"
    if discount is not None:
        row["region"] = "скидка: %.1f%%" % discount

    return row


def _matches_niche(text):
    # type: (str) -> bool
    """Check if text matches our niche keywords (printing/packaging)."""
    text_lower = text.lower()
    niche_keywords = [
        "упаков", "полиграф", "печат", "этикет", "стикер",
        "коробк", "гофр", "картон", "пакет", "конверт",
        "блокнот", "брошюр", "календар", "каталог",
        "bosma", "pechat", "paket", "konvert", "etiket",
        "qadoq", "quti", "yorliq", "stikerlar",
    ]
    for kw in niche_keywords:
        if kw in text_lower:
            return True
    return False


async def update_results(dry_run=False):
    # type: (bool) -> int
    """Fetch completed tender results and upsert into DB.

    Strategy: upsert results as separate records (source='UZEX Результаты')
    with winner, winning_price, discount info. This avoids the ID mismatch
    between TradeList display_no and CivilContracts display_id.

    Returns number of results upserted.
    """
    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.debug("[Results] Supabase not configured, skipping")
        return 0

    from supabase import create_client
    db = create_client(settings.supabase_url, settings.supabase_service_role_key)

    async with httpx.AsyncClient(timeout=20) as client:
        results = await _fetch_uzex_results(client)
        if not results:
            logger.info("[Results] No UZEX results fetched")
            return 0

        logger.info("[Results] Fetched %d UZEX deal results", len(results))

        # Build rows for upsert (deduplicate by external_id)
        seen_ids = {}  # type: Dict[str, dict]
        niche_rows = []
        for item in results:
            row = _build_result_row(item)
            if not row:
                continue
            # Only completed deals
            if row["status"] != "completed":
                continue
            # Deduplicate — keep last occurrence
            seen_ids[row["external_id"]] = row
            if _matches_niche(row["search_text"]):
                niche_rows.append(row)
        rows = list(seen_ids.values())

        if not rows:
            logger.info("[Results] No completed deals to upsert")
            return 0

        logger.info(
            "[Results] %d completed deals (%d in our niche)",
            len(rows), len(niche_rows),
        )

        if dry_run:
            for r in niche_rows[:10]:
                discount_info = r.get("region", "")
                logger.info(
                    "[Results] DRY RUN NICHE: %s | winner=%s | price=%.0f | %s",
                    (r["title"])[:60],
                    (r.get("winner") or "?")[:30],
                    r.get("winning_price") or 0,
                    discount_info,
                )
            return len(rows)

        # Upsert in batches of 500
        upserted = 0
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                db.table("tenders").upsert(
                    batch, on_conflict="external_id,source"
                ).execute()
                upserted += len(batch)
            except Exception as exc:
                logger.error("[Results] Upsert batch %d failed: %s", i, str(exc)[:120])

        logger.info("[Results] Upserted %d result records", upserted)

        # Send alert for niche results — but only for IDs we have not alerted before.
        # Without dedup the same 7-15 niche deals get re-sent on every cron run
        # because UZEX API returns the last 500 completed deals each time.
        if niche_rows:
            sent_order = _load_alerted_ids()  # ordered list (oldest first)
            sent_set = set(sent_order)
            unsent = [r for r in niche_rows if r["external_id"] not in sent_set]
            if not unsent:
                logger.info(
                    "[Results] All %d niche items already alerted — skipping",
                    len(niche_rows),
                )
            else:
                await _alert_niche_results(unsent)
                _record_alerted_ids(sent_order, [r["external_id"] for r in unsent])

        return upserted


def _load_alerted_ids() -> List[str]:
    """Read previously-alerted niche result IDs (oldest first) from crawler_settings."""
    try:
        from crawler.auth.session_store import session_store
        state = session_store.get_setting(_NICHE_ALERTED_STATE_KEY)
        if not isinstance(state, dict):
            return []
        ids = state.get("alerted_ids") or []
        if not isinstance(ids, list):
            return []
        return [str(x) for x in ids if x]
    except Exception as exc:
        logger.warning("[Results] Failed to load alerted_ids: %s", str(exc)[:80])
        return []


def _record_alerted_ids(previously_sent: List[str], newly_sent: List[str]) -> bool:
    """Append newly-sent IDs to the alerted list, FIFO-cap to _NICHE_ALERTED_CAP.

    previously_sent: ordered list (oldest first) — typically what _load_alerted_ids returned.
    newly_sent: ids alerted in the current run, appended to the tail.
    """
    try:
        from crawler.auth.session_store import session_store
        merged: List[str] = []
        seen: Set[str] = set()
        for ext_id in list(previously_sent) + list(newly_sent):
            if ext_id and ext_id not in seen:
                merged.append(ext_id)
                seen.add(ext_id)
        # Trim oldest entries if over cap (keep tail = most recently alerted)
        if len(merged) > _NICHE_ALERTED_CAP:
            merged = merged[-_NICHE_ALERTED_CAP:]
        return session_store.set_setting(
            _NICHE_ALERTED_STATE_KEY,
            {"alerted_ids": merged, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as exc:
        logger.warning("[Results] Failed to save alerted_ids: %s", str(exc)[:80])
        return False


async def _alert_niche_results(niche_rows):
    # type: (List[dict]) -> None
    """Send Telegram alert for completed tenders in our niche with discount %."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return

    try:
        parts = ["*Результаты тендеров (наша ниша):*", ""]

        for row in niche_rows[:7]:
            title = (row.get("title") or "")[:80]
            # Escape markdown
            for ch in ("*", "_", "`", "["):
                title = title.replace(ch, "")

            winner = row.get("winner") or "?"
            for ch in ("*", "_", "`", "["):
                winner = winner.replace(ch, "")

            start_price = row.get("price")
            final_price = row.get("winning_price")
            discount = _calc_discount(start_price, final_price)

            line = "- %s" % title

            # Customer
            org = row.get("organization", "")
            if org:
                for ch in ("*", "_", "`", "["):
                    org = org.replace(ch, "")
                line += "\n  Заказчик: %s" % org[:50]

            # Winner
            line += "\n  Победитель: %s" % winner[:50]

            # Prices + discount
            if start_price and final_price:
                line += "\n  Цена: %s -> %s %s" % (
                    "{:,.0f}".format(start_price),
                    "{:,.0f}".format(final_price),
                    row.get("currency", "UZS"),
                )
                if discount is not None:
                    line += " (-%s%%)" % "{:.1f}".format(discount)
            elif final_price:
                line += "\n  Цена: %s %s" % (
                    "{:,.0f}".format(final_price),
                    row.get("currency", "UZS"),
                )

            parts.append(line)
            parts.append("")  # empty line between items

        text = "\n".join(parts)

        bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
                "disable_notification": True,
            })
            if resp.status_code == 200:
                logger.info(
                    "[Results] Sent niche results alert (%d new of %d candidates)",
                    len(niche_rows[:7]), len(niche_rows),
                )
            else:
                logger.warning("[Results] Telegram send failed: %d %s", resp.status_code, resp.text[:100])
    except Exception as exc:
        logger.warning("[Results] Alert send failed: %s", str(exc)[:80])
