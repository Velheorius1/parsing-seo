"""Supabase upsert logic for tenders."""

import logging
from typing import List, Optional

from crawler.config.settings import settings
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

TABLE = "tenders"
UPSERT_CONFLICT = "external_id,source"


def _get_client():  # type: ignore[no-untyped-def]
    """Lazy-init Supabase client (service_role for writes)."""
    from supabase import create_client

    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _tender_to_row(t: RawTender) -> dict:
    """Convert RawTender to a dict matching the Supabase tenders table schema."""
    return {
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


async def upsert_tenders(
    tenders: List[RawTender],
    batch_size: Optional[int] = None,
    dry_run: bool = False,
) -> int:
    """Upsert tenders into Supabase in batches.

    Returns total upserted count. On dry_run, logs but does not write.
    """
    if not tenders:
        return 0

    if dry_run:
        logger.info("[DB] DRY RUN: would upsert %d tenders", len(tenders))
        return len(tenders)

    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.warning("[DB] Supabase credentials not set, skipping upsert")
        return 0

    size = batch_size or settings.batch_size
    client = _get_client()
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
            logger.error("[DB] Upsert batch %d failed: %s", i, str(exc))

    return total
