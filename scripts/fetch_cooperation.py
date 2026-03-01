#!/usr/bin/env python3
"""Fetch cooperation.uz procurement plans and upsert to Supabase.

Standalone script — runs from Mac (residential IP) since cooperation.uz
blocks all datacenter/cloud IPs.

Usage:
    python3 scripts/fetch_cooperation.py          # fetch & upsert
    python3 scripts/fetch_cooperation.py --dry-run # fetch only, no DB
    python3 scripts/fetch_cooperation.py --pages 5 # fetch 5 pages (default: 3)

Requires: pip install httpx supabase python-dotenv
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Load .env from VPS-style .env or local .env.cooperation
try:
    from dotenv import load_dotenv
    # Try project-level .env first, then .env.cooperation
    env_file = os.path.join(os.path.dirname(__file__), '..', '.env.cooperation')
    if os.path.exists(env_file):
        load_dotenv(env_file)
    else:
        load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

import httpx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────
API_URL = 'https://new.cooperation.uz/ocelot/api-client/Client/GetAllPlanSchedule'
PAGE_SIZE = 500
SOURCE_NAME = 'Cooperation.uz Закупочные планы'
ID_PREFIX = 'coop'

SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')


def _extract_ru(obj: Any) -> str:
    """Extract Russian name from multilingual field like {uz:'', ru:'', ...}."""
    if isinstance(obj, dict):
        return obj.get('ru') or obj.get('uz') or ''
    if isinstance(obj, str):
        return obj
    return ''


def fetch_plans(max_pages: int = 3) -> List[Dict[str, Any]]:
    """Fetch procurement plans from cooperation.uz API."""
    all_items = []  # type: List[Dict[str, Any]]

    with httpx.Client(timeout=20) as client:
        for page in range(max_pages):
            skip = page * PAGE_SIZE
            logger.info('Fetching page %d (Skip=%d, Take=%d)...', page + 1, skip, PAGE_SIZE)

            try:
                resp = client.get(
                    API_URL,
                    params={'Skip': skip, 'Take': PAGE_SIZE},
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                        'Accept': 'application/json',
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                items = data.get('result', {}).get('data', [])
                total = data.get('result', {}).get('total', 0)

                if not items:
                    logger.info('No more items on page %d', page + 1)
                    break

                all_items.extend(items)
                logger.info(
                    'Page %d: %d items (total in DB: %d, fetched so far: %d)',
                    page + 1, len(items), total, len(all_items),
                )

                if len(all_items) >= total:
                    break

            except Exception as exc:
                logger.error('Error on page %d: %s', page + 1, str(exc))
                break

    return all_items


def transform_to_tenders(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transform API items to tender table rows."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []

    for item in items:
        item_id = str(item.get('id', ''))
        if not item_id:
            continue

        title = _extract_ru(item.get('productName', ''))
        if not title:
            continue

        org = _extract_ru(item.get('companyName', ''))
        month = item.get('month', '')
        year = item.get('year', '')
        deadline = '%s/%s' % (month, year) if month and year else None
        category = item.get('manExpencyName', '')

        search_text = ' '.join(filter(None, [title, org, category])).lower()

        rows.append({
            'external_id': '%s-%s' % (ID_PREFIX, item_id),
            'title': title[:500],
            'organization': org[:200] if org else None,
            'price': None,
            'currency': 'UZS',
            'deadline': deadline,
            'date_start': None,
            'date_end': None,
            'region': None,
            'categories': [category] if category else None,
            'source': SOURCE_NAME,
            'source_url': 'https://new.cooperation.uz',
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    return rows


def upsert_to_supabase(rows: List[Dict[str, Any]]) -> int:
    """Upsert tender rows to Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error('SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set')
        return 0

    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    upserted = 0
    batch_size = 500

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        try:
            client.table('tenders').upsert(
                batch, on_conflict='external_id,source'
            ).execute()
            upserted += len(batch)
            logger.info('Upserted batch %d-%d (%d rows)', i, i + len(batch), len(batch))
        except Exception as exc:
            logger.error('Upsert batch %d failed: %s', i, str(exc))

    return upserted


def main() -> None:
    parser = argparse.ArgumentParser(description='Fetch cooperation.uz plans')
    parser.add_argument('--dry-run', action='store_true', help='Fetch only, no DB')
    parser.add_argument('--pages', type=int, default=3, help='Max pages to fetch (default: 3)')
    args = parser.parse_args()

    logger.info('=== Cooperation.uz fetcher START ===')

    items = fetch_plans(max_pages=args.pages)
    if not items:
        logger.warning('No items fetched')
        return

    rows = transform_to_tenders(items)
    logger.info('Transformed %d items -> %d tender rows', len(items), len(rows))

    if args.dry_run:
        logger.info('DRY RUN — would upsert %d rows', len(rows))
        for r in rows[:3]:
            logger.info('  %s | %s', r['title'][:60], r['organization'] or '?')
        return

    upserted = upsert_to_supabase(rows)
    logger.info('=== DONE: upserted %d / %d rows ===', upserted, len(rows))


if __name__ == '__main__':
    main()
