#!/usr/bin/env python3
"""Batch-update cooperation.uz source_url from generic to parameterized planId URLs.

Old: https://new.cooperation.uz/plan-schedule
New: https://new.cooperation.uz/supplier/plans?planId={external_id_without_prefix}

Usage:
    python -m crawler.scripts.update_cooperation_urls [--dry-run] [--limit N]
"""

import argparse
import logging
import time
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

GENERIC_URL = "https://new.cooperation.uz/plan-schedule"
NEW_URL_TEMPLATE = "https://new.cooperation.uz/supplier/plans?planId=%s"
BATCH_SIZE = 200  # conservative to avoid Supabase rate limits
SLEEP_BETWEEN_BATCHES = 1.0  # seconds


def get_client():
    """Init Supabase client with service_role key."""
    from crawler.config.settings import settings
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def find_stale_records(client, limit=None):
    """Find cooperation tenders with generic source_url."""
    query = (
        client.table("tenders")
        .select("id,external_id,source,source_url")
        .like("source", "Cooperation%")
        .eq("source_url", GENERIC_URL)
        .order("created_at", desc=False)
    )
    if limit:
        query = query.limit(limit)
    else:
        query = query.limit(10000)

    result = query.execute()
    return result.data or []


def extract_plan_id(external_id):
    """Extract planId from external_id (format: coop-{planId} or just {planId})."""
    if external_id.startswith("coop-"):
        return external_id[5:]
    return external_id


def update_batch(client, records, dry_run=False):
    """Update a batch of records with correct source_url."""
    updated = 0
    errors = 0

    for rec in records:
        plan_id = extract_plan_id(rec["external_id"])
        new_url = NEW_URL_TEMPLATE % plan_id

        if dry_run:
            logger.info("[DRY-RUN] %s -> %s", rec["external_id"], new_url)
            updated += 1
            continue

        try:
            client.table("tenders").update(
                {"source_url": new_url}
            ).eq("id", rec["id"]).execute()
            updated += 1
        except Exception as exc:
            logger.warning("Failed to update %s: %s", rec["id"], str(exc)[:80])
            errors += 1

    return updated, errors


def main():
    parser = argparse.ArgumentParser(description="Update cooperation source_url")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    parser.add_argument("--limit", type=int, default=None, help="Max records to update")
    args = parser.parse_args()

    client = get_client()

    logger.info("Finding stale cooperation records...")
    records = find_stale_records(client, limit=args.limit)
    logger.info("Found %d records with generic URL", len(records))

    if not records:
        logger.info("Nothing to update!")
        return

    total_updated = 0
    total_errors = 0

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        logger.info(
            "Processing batch %d/%d (%d records)...",
            i // BATCH_SIZE + 1,
            (len(records) + BATCH_SIZE - 1) // BATCH_SIZE,
            len(batch),
        )

        updated, errors = update_batch(client, batch, dry_run=args.dry_run)
        total_updated += updated
        total_errors += errors

        if not args.dry_run and i + BATCH_SIZE < len(records):
            time.sleep(SLEEP_BETWEEN_BATCHES)

    logger.info(
        "Done! Updated: %d, Errors: %d, Total: %d",
        total_updated, total_errors, len(records),
    )


if __name__ == "__main__":
    main()
