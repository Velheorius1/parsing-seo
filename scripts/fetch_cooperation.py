#!/usr/bin/env python3
"""Fetch cooperation.uz procurement plans and upsert to Supabase.

Standalone script — runs from Mac (residential IP) since cooperation.uz
blocks all datacenter/cloud IPs.

Usage:
    python3 scripts/fetch_cooperation.py          # fetch & upsert + alerts
    python3 scripts/fetch_cooperation.py --dry-run # fetch only, no DB
    python3 scripts/fetch_cooperation.py --pages 5 # fetch 5 pages (default: 3)

Requires: pip install httpx supabase python-dotenv
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

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

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_ALERT_CHAT_ID', '')

# Same 37 keywords as crawler notifier
ALERT_KEYWORDS = (
    'упаковка,полиграфия,гофра,коробка,печать,этикетка,типография,'
    'книга,книж,каталог,брошюр,блокнот,календар,пакет,конверт,папка,'
    'ежедневник,сувенир,журнал,картон,подарочн,зонт,ручка,флешк,'
    'power bank,набор,плакат,постер,стенд,вывеск,'
    'packaging,printing,cardboard,label,box,qadoqlash,bosma'
)

_MIN_STEM = 4


def _stem(word):
    # type: (str) -> str
    """Crude Russian stemming."""
    if len(word) <= _MIN_STEM:
        return word
    for suffix in ('ция', 'ия', 'ка', 'ок', 'ей', 'ов', 'ть', 'ые', 'ой', 'ая', 'ое', 'а', 'о', 'е', 'и', 'у', 'ы'):
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[:-len(suffix)]
    return word


def _word_start_match(text, stem):
    # type: (str, str) -> int
    """Find stem at word boundary."""
    start = 0
    while True:
        idx = text.find(stem, start)
        if idx == -1:
            return -1
        if idx == 0 or not text[idx - 1].isalpha():
            return idx
        start = idx + 1


_FALSE_POSITIVES = {
    'календар': [' кун', 'кун ', ' дн', ' день'],
}


def _find_matching_keyword(title, search_text, keywords):
    # type: (str, str, List[str]) -> Optional[str]
    """Return first matching keyword or None."""
    text = (search_text + ' ' + title).lower()
    for kw in keywords:
        stem = _stem(kw) if len(kw) > _MIN_STEM else kw
        if len(stem) < _MIN_STEM:
            if _word_start_match(text, kw) >= 0:
                return kw
            continue
        idx = _word_start_match(text, stem)
        if idx < 0:
            continue
        excl = _FALSE_POSITIVES.get(stem)
        if excl:
            after = text[idx + len(stem):idx + len(stem) + 10]
            if any(after.startswith(fp) for fp in excl):
                continue
        return kw
    return None


def _extract_ru(obj):
    # type: (Any) -> str
    """Extract Russian name from multilingual field like {uz:'', ru:'', ...}."""
    if isinstance(obj, dict):
        return obj.get('ru') or obj.get('uz') or ''
    if isinstance(obj, str):
        return obj
    return ''


def fetch_plans(max_pages=3):
    # type: (int) -> List[Dict[str, Any]]
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


def transform_to_tenders(items):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
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


def get_existing_ids():
    # type: () -> Set[str]
    """Get existing external_ids from Supabase for this source."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()

    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    existing = set()  # type: Set[str]
    offset = 0
    batch = 1000
    while True:
        resp = client.table('tenders').select('external_id').eq(
            'source', SOURCE_NAME
        ).range(offset, offset + batch - 1).execute()
        rows = resp.data or []
        for r in rows:
            existing.add(r['external_id'])
        if len(rows) < batch:
            break
        offset += batch

    return existing


def upsert_to_supabase(rows):
    # type: (List[Dict[str, Any]]) -> int
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


def send_alerts(new_rows):
    # type: (List[Dict[str, Any]]) -> int
    """Send Telegram alerts for new tenders matching keywords."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning('TELEGRAM_BOT_TOKEN or TELEGRAM_ALERT_CHAT_ID not set, skipping alerts')
        return 0

    keywords = [k.strip().lower() for k in ALERT_KEYWORDS.split(',') if k.strip()]
    if not keywords:
        return 0

    # Find matching tenders
    matching = []  # type: List[tuple]
    for row in new_rows:
        kw = _find_matching_keyword(row['title'], row.get('search_text', ''), keywords)
        if kw:
            matching.append((row, kw))

    if not matching:
        logger.info('[Alerts] No new tenders match keywords (%d checked)', len(new_rows))
        return 0

    logger.info('[Alerts] %d new tenders match keywords', len(matching))

    # Send to Telegram
    bot_url = 'https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN
    sent = 0

    with httpx.Client(timeout=10) as client:
        for row, kw in matching:
            parts = []
            parts.append('*%s*' % row['title'][:200].replace('*', '').replace('_', '').replace('`', '').replace('[', ''))
            if row.get('organization'):
                parts.append('Заказчик: %s' % row['organization'])
            if row.get('deadline'):
                parts.append('Период: %s' % row['deadline'])
            parts.append('Источник: %s' % SOURCE_NAME)
            parts.append('https://new.cooperation.uz')
            parts.append('#%s' % kw.replace(' ', '_'))
            text = '\n'.join(parts)

            try:
                resp = client.post(bot_url, json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': text,
                    'parse_mode': 'Markdown',
                    'disable_web_page_preview': True,
                    'protect_content': True,
                })
                if resp.status_code == 200:
                    sent += 1
                else:
                    logger.warning('[Alerts] Telegram %d: %s', resp.status_code, resp.text[:200])
            except Exception as exc:
                logger.warning('[Alerts] Error: %s', str(exc))

    logger.info('[Alerts] Sent %d / %d alerts', sent, len(matching))
    return sent


def main():
    # type: () -> None
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

    # Find NEW rows (not yet in Supabase)
    existing_ids = get_existing_ids()
    new_rows = [r for r in rows if r['external_id'] not in existing_ids]
    logger.info('New tenders: %d (existing: %d)', len(new_rows), len(existing_ids))

    # Upsert all rows
    upserted = upsert_to_supabase(rows)
    logger.info('Upserted %d / %d rows', upserted, len(rows))

    # Send alerts for NEW rows only
    if new_rows:
        alerts = send_alerts(new_rows)
        logger.info('=== DONE: upserted %d, alerts %d (new %d) ===', upserted, alerts, len(new_rows))
    else:
        logger.info('=== DONE: upserted %d, no new tenders ===', upserted)


if __name__ == '__main__':
    main()
