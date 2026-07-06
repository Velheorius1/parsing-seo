#!/usr/bin/env python3
"""Fetch UZEX reverse auctions and prequalifications, upsert to Supabase.

Standalone script — runs from Mac (residential IP) since UZEX blocks
datacenter/cloud IPs (geo-restricted).

Sources:
  1. Lot/GetList — reverse auctions (обратные аукционы)
  2. Public/GetLots — prequalifications (предквалификации)

Usage:
    python3 scripts/fetch_uzex_auctions.py              # all sources
    python3 scripts/fetch_uzex_auctions.py --dry-run     # fetch only, no DB
    python3 scripts/fetch_uzex_auctions.py --source auctions
    python3 scripts/fetch_uzex_auctions.py --source prequest

Requires: pip install httpx supabase python-dotenv
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
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

SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_ALERT_CHAT_ID = os.getenv('TELEGRAM_ALERT_CHAT_ID', '')

# ── Proxy config (Vercel Edge — bypasses geo-block) ──
PROXY_URL = os.getenv('COOPERATION_PROXY_URL', 'https://parsing-seo.vercel.app/api/proxy/cooperation')
PROXY_SECRET = os.getenv('PROXY_SECRET', '')


def _send_telegram_alert(text):
    # type: (str) -> None
    """Send alert to Telegram via Bot API. Silently fails if tokens missing."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALERT_CHAT_ID:
        logger.warning('Telegram alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_ALERT_CHAT_ID not set')
        return
    try:
        url = 'https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN
        r = httpx.post(url, json={
            'chat_id': TELEGRAM_ALERT_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML',
        }, timeout=10, trust_env=False)
        if r.status_code != 200:
            logger.warning('Telegram alert failed: %s', r.text[:100])
    except Exception as exc:
        logger.warning('Telegram alert error: %s', str(exc)[:80])

HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
}

# Sticky proxy state: once proxy works, stay on proxy for remaining pages
_use_proxy = False


def _should_use_proxy(exc):
    # type: (Exception) -> bool
    """Check if exception indicates geo-block / network issue worth retrying via proxy."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (403, 451, 502, 503)
    return False


def _fetch_via_proxy(proxy_endpoint, from_val, to_val):
    # type: (str, int, int) -> Any
    """Fetch data through Vercel Edge proxy (GET with query params). Returns parsed JSON or raises."""
    params = {
        'endpoint': proxy_endpoint,
        'from': str(from_val),
        'to': str(to_val),
    }
    r = httpx.get(
        PROXY_URL,
        params=params,
        headers={'X-Proxy-Key': PROXY_SECRET, 'Accept': 'application/json'},
        timeout=25,
    )
    r.raise_for_status()
    return r.json()

# ── UZEX API endpoints ──

AUCTIONS_URL = 'https://xarid-api-auctionx.uzex.uz/api/Lot/GetList'
PREQUEST_URL = 'https://xarid-api-prequest.uzex.uz/api/Public/GetLots'


def fetch_auctions(max_items=500):
    # type: (int) -> tuple
    """Fetch reverse auctions from UZEX. Returns (items, error_count)."""
    global _use_proxy
    all_items = []  # type: List[Dict[str, Any]]
    page_size = 100
    offset = 0
    errors = 0

    while offset < max_items:
        data = None

        # Try direct first (unless proxy already succeeded on a previous page)
        if not _use_proxy:
            try:
                r = httpx.post(
                    AUCTIONS_URL,
                    json={'from': offset, 'to': offset + page_size},
                    headers=HEADERS,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                if _should_use_proxy(exc):
                    logger.warning('Auctions direct failed (%s), trying proxy...', str(exc)[:80])
                    _use_proxy = True
                else:
                    errors += 1
                    logger.warning('Auctions fetch error at offset %d: %s', offset, str(exc)[:80])
                    break

        # Proxy fallback (or sticky proxy mode)
        if data is None and _use_proxy:
            try:
                data = _fetch_via_proxy('UzexAuctionGetList', offset, offset + page_size)
                logger.info('Auctions: offset %d fetched via proxy', offset)
            except Exception as exc:
                errors += 1
                logger.warning('Auctions proxy also failed at offset %d: %s', offset, str(exc)[:80])
                break

        if data is None:
            break

        items = data.get('Data', [])
        if not items:
            break
        all_items.extend(items)
        logger.info('Auctions: fetched %d (offset %d)', len(items), offset)
        if len(items) < page_size:
            break
        offset += page_size

    return all_items, errors


def fetch_prequalifications(max_items=1000):
    # type: (int) -> tuple
    """Fetch prequalification lots from UZEX. Returns (items, error_count)."""
    global _use_proxy
    all_items = []  # type: List[Dict[str, Any]]
    page_size = 500
    offset = 0
    errors = 0

    while offset < max_items:
        data = None

        # Try direct first (unless proxy already succeeded on a previous page)
        if not _use_proxy:
            try:
                r = httpx.post(
                    PREQUEST_URL,
                    json={'from': offset, 'to': offset + page_size},
                    headers=HEADERS,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                if _should_use_proxy(exc):
                    logger.warning('Prequest direct failed (%s), trying proxy...', str(exc)[:80])
                    _use_proxy = True
                else:
                    errors += 1
                    logger.warning('Prequest fetch error at offset %d: %s', offset, str(exc)[:80])
                    break

        # Proxy fallback (or sticky proxy mode)
        if data is None and _use_proxy:
            try:
                data = _fetch_via_proxy('UzexPrequestGetLots', offset, offset + page_size)
                logger.info('Prequest: offset %d fetched via proxy', offset)
            except Exception as exc:
                errors += 1
                logger.warning('Prequest proxy also failed at offset %d: %s', offset, str(exc)[:80])
                break

        if data is None:
            break

        items = data.get('Data', [])
        if not items:
            break
        all_items.extend(items)
        logger.info('Prequest: fetched %d (offset %d)', len(items), offset)
        if len(items) < page_size:
            break
        offset += page_size

    return all_items, errors


def to_tender_row(item, source_name, id_prefix):
    # type: (Dict[str, Any], str, str) -> Dict[str, Any]
    """Convert raw API item to tenders table row."""
    ext_id = str(item.get('id', ''))
    return {
        'external_id': '%s-%s' % (id_prefix, ext_id),
        'title': item.get('categoryName', '') or '',
        'organization': item.get('customerName', '') or '',
        'price': item.get('startCost') or None,
        'currency': 'UZS',
        'deadline': item.get('endDate', '') or '',
        'date_start': item.get('startDate', '') or '',
        'date_end': item.get('endDate', '') or '',
        'region': item.get('regionName', '') or '',
        'source': source_name,
        # 2026-07-06 (weekly routine): prequest ids live in the new-xarid
        # proposal-request space. Old xarid.uzex.uz/prequalification/detail/{id}
        # redirects to homepage (browser-verified broken); new-xarid
        # proposal-request/detail/{id} opens the real lot anonymously
        # (83758 -> "Услуги профессиональные, научные и технические").
        # Mirrors the sources.yaml uzex-prequest fix that this script bypassed.
        'source_url': 'https://xarid.uzex.uz/auction/detail/%s' % ext_id if id_prefix == 'uzex-auc' else 'https://new-xarid.uzex.uz/home/purchase/proposal-request/detail/%s' % ext_id,
        'status': 'active',
        'search_text': ' '.join(filter(None, [
            item.get('categoryName', ''),
            item.get('customerName', ''),
            item.get('regionName', ''),
            item.get('description', ''),
        ])),
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'message_type': 'tender',
    }


def upsert_to_supabase(rows, dry_run=False):
    # type: (List[Dict[str, Any]], bool) -> int
    """Upsert rows to Supabase tenders table."""
    if not rows:
        return 0
    if dry_run:
        logger.info('[DRY-RUN] Would upsert %d rows', len(rows))
        for r in rows[:3]:
            logger.info('  %s: %s (%s)', r['external_id'], r['title'][:50], r['source'])
        return len(rows)

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error('SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set')
        return 0

    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    batch_size = 500
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        # Retry upsert up to 3 times with exponential backoff
        last_exc = None
        for attempt in range(1, 4):
            try:
                client.table('tenders').upsert(
                    batch, on_conflict='external_id,source'
                ).execute()
                total += len(batch)
                logger.info('Upserted batch %d/%d (%d rows)',
                            i // batch_size + 1,
                            (len(rows) + batch_size - 1) // batch_size,
                            len(batch))
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 3:
                    delay = 2 ** (attempt - 1)
                    logger.warning('Upsert attempt %d/3 failed: %s. Retrying in %ds...',
                                   attempt, str(exc)[:80], delay)
                    time.sleep(delay)
                else:
                    logger.error('Upsert failed after 3 attempts: %s', str(exc)[:120])

    return total


def main():
    parser = argparse.ArgumentParser(description='Fetch UZEX auctions')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--source', choices=['auctions', 'prequest', 'all'], default='all')
    args = parser.parse_args()

    total_fetched = 0
    total_upserted = 0
    total_errors = 0

    if args.source in ('auctions', 'all'):
        logger.info('=== Fetching UZEX Reverse Auctions ===')
        items, errs = fetch_auctions()
        total_errors += errs
        logger.info('Fetched %d auctions', len(items))
        rows = [to_tender_row(it, 'UZEX Обратные аукционы', 'uzex-auc') for it in items]
        total_fetched += len(rows)
        total_upserted += upsert_to_supabase(rows, dry_run=args.dry_run)

    if args.source in ('prequest', 'all'):
        logger.info('=== Fetching UZEX Prequalifications ===')
        items, errs = fetch_prequalifications()
        total_errors += errs
        logger.info('Fetched %d prequalifications', len(items))
        rows = [to_tender_row(it, 'UZEX Предквалификации', 'uzex-prq') for it in items]
        total_fetched += len(rows)
        total_upserted += upsert_to_supabase(rows, dry_run=args.dry_run)

    logger.info('Done! Fetched: %d, Upserted: %d, Errors: %d', total_fetched, total_upserted, total_errors)

    if total_fetched == 0 and total_errors > 0:
        msg = '<b>UZEX Auctions ALERT</b>\nFetched 0 items with %d errors.\nSource: %s' % (total_errors, args.source)
        _send_telegram_alert(msg)
        sys.exit(1)


if __name__ == '__main__':
    main()
