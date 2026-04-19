"""Supabase upsert logic for tenders."""

import logging
from typing import List, Optional, Set, Tuple

from crawler.config.settings import settings
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

TABLE = "tenders"
UPSERT_CONFLICT = "external_id,source"



_client = None  # type: ignore[assignment]


def _get_client():  # type: ignore[no-untyped-def]
    """Lazy-init Supabase client singleton (service_role for writes)."""
    global _client
    if _client is None:
        from supabase import create_client
        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client


def _tender_to_row(t: RawTender) -> dict:
    """Convert RawTender to a dict matching the Supabase tenders table schema."""
    row = {
        "external_id": t.external_id,
        "title": t.title,
        "organization": t.organization,
        "price": t.price,
        "currency": t.currency,
        "deadline": t.deadline,
        "date_start": t.date_start,
        "date_end": t.date_end,
        "region": t.region,
        "categories": t.categories,
        "source": t.source,
        "source_url": t.source_url,
        "status": t.status,
        "search_text": t.search_text,
        "collected_at": t.collected_at.isoformat(),
    }
    row["message_type"] = t.message_type
    # Persist enriched display fields (Район, Адрес, Количество, Цена/ед., и т.д.)
    # Column added in migration 015; harmless if column absent (Supabase ignores unknown keys).
    if t.extra_info:
        row["extra_info"] = t.extra_info
    # Optional result fields — only include if set
    if t.winner:
        row["winner"] = t.winner
    if t.winning_price is not None:
        row["winning_price"] = t.winning_price
    if t.result_date:
        row["result_date"] = t.result_date
    if t.group_id:
        row["group_id"] = t.group_id
    return row


def _get_existing_keys(
    client,  # type: ignore[no-untyped-def]
    tenders: List[RawTender],
) -> Set[Tuple[str, str]]:
    """Fetch existing (external_id, source) pairs from DB for the given tenders.

    Returns set of tuples that already exist in the database.
    """
    existing = set()  # type: Set[Tuple[str, str]]
    # Group by source to minimize queries
    sources = set(t.source for t in tenders)
    for source in sources:
        source_ids = [t.external_id for t in tenders if t.source == source]
        # Query in batches of 500 (Supabase filter limit)
        for i in range(0, len(source_ids), 500):
            batch_ids = source_ids[i : i + 500]
            try:
                resp = (
                    client.table(TABLE)
                    .select("external_id,source")
                    .eq("source", source)
                    .in_("external_id", batch_ids)
                    .execute()
                )
                for row in resp.data:
                    existing.add((row["external_id"], row["source"]))
            except Exception as exc:
                logger.warning("[DB] Failed to check existing for %s: %s", source, str(exc))
    return existing


async def upsert_tenders(
    tenders: List[RawTender],
    batch_size: Optional[int] = None,
    dry_run: bool = False,
) -> Tuple[int, List[RawTender]]:
    """Upsert tenders into Supabase in batches.

    Returns (total_upserted, list_of_new_tenders).
    """
    if not tenders:
        return 0, []

    # Deduplicate by (external_id, source) — keep last occurrence
    seen = {}
    for t in tenders:
        seen[(t.external_id, t.source)] = t
    tenders = list(seen.values())
    logger.info("[DB] Deduplicated: %d unique tenders", len(tenders))

    if dry_run:
        logger.info("[DB] DRY RUN: would upsert %d tenders", len(tenders))
        return len(tenders), tenders

    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.warning("[DB] Supabase credentials not set, skipping upsert")
        return 0, []

    client = _get_client()

    # Find which tenders are NEW (not in DB yet)
    existing_keys = _get_existing_keys(client, tenders)
    new_tenders = [
        t for t in tenders
        if (t.external_id, t.source) not in existing_keys
    ]
    logger.info("[DB] New tenders: %d (existing: %d)", len(new_tenders), len(existing_keys))

    size = batch_size or settings.batch_size
    total = 0

    for i in range(0, len(tenders), size):
        batch = tenders[i : i + size]
        rows = [_tender_to_row(t) for t in batch]
        try:
            client.table(TABLE).upsert(
                rows, on_conflict=UPSERT_CONFLICT
            ).execute()
            total += len(batch)
            logger.info(
                "[DB] Upserted batch %d-%d (%d rows)",
                i,
                i + len(batch),
                len(batch),
            )
        except Exception as exc:
            msg = str(exc)
            # Fallback: retry without extra_info if the column is not yet deployed
            # (migration 015 pending). Prevents write downtime during rollout.
            if "extra_info" in msg and ("PGRST204" in msg or "column" in msg.lower()):
                logger.warning(
                    "[DB] extra_info column missing — retrying batch %d without it", i
                )
                stripped = [{k: v for k, v in r.items() if k != "extra_info"} for r in rows]
                try:
                    client.table(TABLE).upsert(
                        stripped, on_conflict=UPSERT_CONFLICT
                    ).execute()
                    total += len(batch)
                    logger.info(
                        "[DB] Upserted batch %d-%d without extra_info (%d rows)",
                        i, i + len(batch), len(batch),
                    )
                    continue
                except Exception as exc2:
                    logger.error("[DB] Fallback upsert batch %d failed: %s", i, str(exc2))
            logger.error("[DB] Upsert batch %d failed: %s", i, msg)

    return total, new_tenders
