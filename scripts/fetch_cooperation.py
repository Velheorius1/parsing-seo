#!/usr/bin/env python3
"""Fetch cooperation.uz data and upsert to Supabase with Telegram alerts.

Standalone script — runs from Mac (residential IP) since cooperation.uz
blocks all datacenter/cloud IPs.

Sources:
  1. GetAllPlanSchedule — procurement plans (375k total, fetch newest 1500)
  2. GetAllOffer — supplier offers / e-shop (63k total, fetch newest 1000)
  3. GetLotsInTrade — active trade lots / reverse tenders (2.5k, fetch all)

Usage:
    python3 scripts/fetch_cooperation.py              # all sources
    python3 scripts/fetch_cooperation.py --dry-run     # fetch only, no DB
    python3 scripts/fetch_cooperation.py --source plans # only plans
    python3 scripts/fetch_cooperation.py --source offers # only offers
    python3 scripts/fetch_cooperation.py --source lots   # only lots

Requires: pip install httpx supabase python-dotenv
"""

import argparse
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

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

# ── Config ──────────────────────────────────────────────────────
SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_ALERT_CHAT_ID', '')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
AI_MODEL = os.getenv('AI_RELEVANCE_MODEL', 'qwen/qwen3-30b-a3b')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Accept': 'application/json',
}

ALERT_KEYWORDS = (
    'упаковка,полиграфия,гофра,коробка,печать,этикетка,типография,'
    'книга,книж,каталог,брошюр,блокнот,календар,пакет,конверт,папка,'
    'ежедневник,сувенир,журнал,картон,подарочн,зонт,ручка,флешк,'
    'power bank,набор,плакат,постер,стенд,вывеск,'
    'packaging,printing,cardboard,label,box,qadoqlash,bosma'
)

_MIN_STEM = 4


# ── Keyword matching (same logic as crawler notifier) ───────────

def _stem(word):
    # type: (str) -> str
    if len(word) <= _MIN_STEM:
        return word
    for suffix in ('ция', 'ия', 'ка', 'ок', 'ей', 'ов', 'ть', 'ые', 'ой', 'ая', 'ое', 'а', 'о', 'е', 'и', 'у', 'ы'):
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[:-len(suffix)]
    return word


def _word_start_match(text, stem):
    # type: (str, str) -> int
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
    if isinstance(obj, dict):
        return obj.get('ru') or obj.get('uz') or ''
    if isinstance(obj, str):
        return obj
    return ''


# ── Generic API fetcher ─────────────────────────────────────────

def fetch_api(url, page_size, max_pages, response_path='result.data', total_path='result.total'):
    # type: (str, int, int, str, str) -> Tuple[List[Dict[str, Any]], int]
    """Fetch paginated API. Returns (items, total_count)."""
    all_items = []  # type: List[Dict[str, Any]]
    total_count = 0

    with httpx.Client(timeout=30) as client:
        for page in range(max_pages):
            skip = page * page_size
            logger.info('  Fetching page %d (Skip=%d, Take=%d)...', page + 1, skip, page_size)

            try:
                resp = client.get(
                    url,
                    params={'Skip': skip, 'Take': page_size},
                    headers=HEADERS,
                )
                resp.raise_for_status()
                data = resp.json()

                # Navigate response path
                items = data
                for key in response_path.split('.'):
                    items = items.get(key, {}) if isinstance(items, dict) else items
                if not isinstance(items, list):
                    items = []

                # Get total count
                total_obj = data
                for key in total_path.split('.'):
                    total_obj = total_obj.get(key, 0) if isinstance(total_obj, dict) else total_obj
                if isinstance(total_obj, int):
                    total_count = total_obj

                if not items:
                    logger.info('  No more items on page %d', page + 1)
                    break

                all_items.extend(items)
                logger.info(
                    '  Page %d: %d items (total: %d, fetched: %d)',
                    page + 1, len(items), total_count, len(all_items),
                )

                if total_count and len(all_items) >= total_count:
                    break
                if len(items) < page_size:
                    break

            except Exception as exc:
                logger.error('  Error on page %d: %s', page + 1, str(exc))
                break

    return all_items, total_count


# ── Source: Plans ────────────────────────────────────────────────

def fetch_and_transform_plans(max_pages=3):
    # type: (int) -> List[Dict[str, Any]]
    """Fetch procurement plans (GetAllPlanSchedule)."""
    logger.info('[Plans] Fetching procurement plans...')
    items, total = fetch_api(
        'https://new.cooperation.uz/ocelot/api-client/Client/GetAllPlanSchedule',
        page_size=500, max_pages=max_pages,
    )
    if not items:
        return []

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
            'external_id': 'coop-%s' % item_id,
            'title': title[:500],
            'organization': org[:200] if org else None,
            'price': None,
            'currency': 'UZS',
            'deadline': deadline,
            'source': 'Cooperation.uz Закупочные планы',
            'source_url': 'https://new.cooperation.uz',
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[Plans] Transformed %d -> %d rows', len(items), len(rows))
    return rows


# ── Source: Offers (e-shop) ──────────────────────────────────────

def fetch_and_transform_offers(max_pages=5):
    # type: (int) -> List[Dict[str, Any]]
    """Fetch supplier offers (GetAllOffer) — e-shop / reverse tender listings."""
    logger.info('[Offers] Fetching supplier offers...')
    items, total = fetch_api(
        'https://new.cooperation.uz/ocelot/api-client/Client/GetAllOffer',
        page_size=200, max_pages=max_pages,
    )
    if not items:
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in items:
        offer_num = item.get('offerNumber', '')
        if not offer_num:
            continue
        title = _extract_ru(item.get('productName', ''))
        if not title:
            continue

        price = item.get('unitPrice')
        end_date = item.get('publicEndDate', '')
        measure = item.get('measureName', '')
        qty = item.get('productQuantity', '')
        category = item.get('category')
        cat_name = _extract_ru(category.get('name', '')) if isinstance(category, dict) else ''
        tnved = item.get('code', '')

        price_info = ''
        if price:
            price_info = '{:,.0f} UZS'.format(price)
        qty_info = ''
        if qty and measure:
            qty_info = '%s %s' % (qty, measure)

        search_text = ' '.join(filter(None, [title, cat_name, tnved, qty_info])).lower()

        rows.append({
            'external_id': 'coop-offer-%s' % offer_num,
            'title': title[:500],
            'organization': None,  # offers don't expose company name
            'price': float(price) if price else None,
            'currency': 'UZS',
            'deadline': end_date[:10] if end_date else None,
            'source': 'Cooperation.uz Оферты',
            'source_url': 'https://new.cooperation.uz',
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[Offers] Transformed %d -> %d rows', len(items), len(rows))
    return rows


# ── Source: Lots in Trade (reverse tenders) ──────────────────────

def fetch_and_transform_lots():
    # type: () -> List[Dict[str, Any]]
    """Fetch active trade lots (GetLotsInTrade) — reverse tenders, 4-day window."""
    logger.info('[Lots] Fetching active trade lots...')
    items, total = fetch_api(
        'https://new.cooperation.uz/ocelot/api-shop/LotRequest/GetLotsInTrade',
        page_size=2500, max_pages=1,
        response_path='result.result', total_path='result.count',
    )
    if not items:
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in items:
        lot_num = item.get('lotNumber', '')
        if not lot_num:
            continue
        title = item.get('productName', '')
        if not title:
            continue

        offer_num = item.get('offerNumber', '')
        qty = item.get('quantity', '')
        begin = item.get('beginDate', '')
        end = item.get('endDate', '')
        tnved = item.get('lotTnved', '') or ''

        # NOTE: measureName excluded from search_text — "упаковка" as unit
        # triggers false positives on medicines/pharma
        search_text = ' '.join(filter(None, [title, tnved])).lower()

        rows.append({
            'external_id': 'coop-lot-%s' % lot_num,
            'title': title[:500],
            'organization': None,
            'price': None,
            'currency': 'UZS',
            'deadline': end[:10] if end else None,
            'date_start': begin[:10] if begin else None,
            'date_end': end[:10] if end else None,
            'source': 'Cooperation.uz Лоты',
            'source_url': 'https://new.cooperation.uz',
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[Lots] Transformed %d -> %d rows', len(items), len(rows))
    return rows


# ── Source: Auction Lots (cabinet API — buyer created reverse auction) ──

def fetch_and_transform_auction_lots():
    # type: () -> List[Dict[str, Any]]
    """Fetch active auction lots from cabinet API — these are BUYER-initiated reverse auctions."""
    logger.info('[AuctionLots] Fetching active auction lots...')
    all_items = []  # type: List[Dict[str, Any]]

    with httpx.Client(timeout=30) as client:
        try:
            resp = client.get(
                'https://cabinet.cooperation.uz/api/auction/public/lots',
                params={'skip': 0, 'take': 500},
                headers=HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            # Response format: {code: 200, message: "OK", data: [...]}
            if isinstance(data, dict):
                items = data.get('data', [])
                if isinstance(items, list):
                    all_items = items
            elif isinstance(data, list):
                all_items = data
            logger.info('[AuctionLots] Got %d items from API', len(all_items))
        except Exception as exc:
            logger.error('[AuctionLots] Error: %s', str(exc))

    if not all_items:
        logger.info('[AuctionLots] No items found')
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in all_items:
        lot_id = str(item.get('id', '') or item.get('lotNumber', ''))
        if not lot_id:
            continue
        lot_num = item.get('lotNumber', lot_id)
        title = _extract_ru(item.get('name', '')) or _extract_ru(item.get('productName', ''))
        if not title:
            continue

        org = _extract_ru(item.get('companyName', ''))
        region = _extract_ru(item.get('region', ''))
        start_price = item.get('startPrice')
        current_price = item.get('price')
        providers = item.get('providerCount', 0)
        end_date = item.get('endDate', '') or ''
        begin_date = item.get('beginDate', '') or ''

        price_val = current_price or start_price
        search_text = ' '.join(filter(None, [title, org, region])).lower()

        rows.append({
            'external_id': 'coop-auc-%s' % lot_num,
            'title': title[:500],
            'organization': org[:200] if org else None,
            'price': float(price_val) if price_val else None,
            'currency': 'UZS',
            'deadline': end_date[:10] if end_date else None,
            'date_start': begin_date[:10] if begin_date else None,
            'date_end': end_date[:10] if end_date else None,
            'source': 'Cooperation.uz Аукционы',
            'source_url': 'https://new.cooperation.uz',
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[AuctionLots] Transformed %d -> %d rows', len(all_items), len(rows))
    return rows


# ── Source: E-Shop Active Lots (buyer selected product → trade started) ──

def fetch_and_transform_eshop_lots():
    # type: () -> List[Dict[str, Any]]
    """Fetch active e-shop lots — these are BUYER-selected products now in trade."""
    logger.info('[EshopLots] Fetching active e-shop lots...')
    all_items = []  # type: List[Dict[str, Any]]

    with httpx.Client(timeout=30) as client:
        try:
            resp = client.get(
                'https://cabinet.cooperation.uz/api/eshop/lots/active',
                params={'skip': 0, 'take': 500},
                headers=HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            # Response format: {code: 200, message: "OK", data: [...]}
            if isinstance(data, dict):
                items = data.get('data', [])
                if isinstance(items, list):
                    all_items = items
            elif isinstance(data, list):
                all_items = data
            logger.info('[EshopLots] Got %d items from API', len(all_items))
        except Exception as exc:
            logger.error('[EshopLots] Error: %s', str(exc))

    if not all_items:
        logger.info('[EshopLots] No items found')
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in all_items:
        lot_id = str(item.get('id', '') or item.get('lotNumber', ''))
        if not lot_id:
            continue
        lot_num = item.get('lotNumber', lot_id)
        title = _extract_ru(item.get('productName', ''))
        if not title:
            continue

        enkt = item.get('enktCode', '') or ''
        qty = item.get('quantity', '')
        measure = _extract_ru(item.get('measure', ''))
        end_date = item.get('endDate', '') or ''
        begin_date = item.get('startDate', '') or ''

        # NOTE: no measureName in search_text — "упаковка" as unit triggers false positives
        search_text = ' '.join(filter(None, [title, enkt])).lower()

        qty_info = ''
        if qty and measure:
            qty_info = '%s %s' % (qty, measure)

        rows.append({
            'external_id': 'coop-eshop-%s' % lot_num,
            'title': ('%s (%s)' % (title, qty_info) if qty_info else title)[:500],
            'organization': None,
            'price': None,
            'currency': 'UZS',
            'deadline': end_date[:10] if end_date else None,
            'date_start': begin_date[:10] if begin_date else None,
            'date_end': end_date[:10] if end_date else None,
            'source': 'Cooperation.uz Э-магазин лоты',
            'source_url': 'https://new.cooperation.uz',
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[EshopLots] Transformed %d -> %d rows', len(all_items), len(rows))
    return rows


# ── Source: UZEX Reverse Auctions (GEO-BLOCKED from VPS) ─────────

def fetch_and_transform_uzex_auctions():
    # type: () -> List[Dict[str, Any]]
    """Fetch UZEX reverse auction lots — buyer-initiated, active bidding."""
    logger.info('[UZEX-Auc] Fetching reverse auction lots...')
    all_items = []  # type: List[Dict[str, Any]]

    with httpx.Client(timeout=30) as client:
        try:
            resp = client.post(
                'https://xarid-api-auctionx.uzex.uz/api/Lot/GetList',
                json={'from': 0, 'to': 200},
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                },
            )
            resp.raise_for_status()
            data = resp.json()
            # Response: {Status: 200, Data: [...]}
            if isinstance(data, dict):
                items = data.get('Data', [])
                if isinstance(items, list):
                    all_items = items
            logger.info('[UZEX-Auc] Got %d items from API', len(all_items))
        except Exception as exc:
            logger.error('[UZEX-Auc] Error: %s', str(exc))

    if not all_items:
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in all_items:
        lot_id = str(item.get('id', ''))
        if not lot_id:
            continue
        display_no = item.get('displayNo', lot_id)
        title = item.get('categoryName', '')
        if not title:
            continue

        region = item.get('regionName', '')
        district = item.get('districtName', '')
        start_cost = item.get('startCost')
        next_cost = item.get('nextCost')
        end_date = item.get('endDate', '') or ''
        pcp_count = item.get('pcpCount', 0)

        search_text = ' '.join(filter(None, [title, region, district])).lower()

        rows.append({
            'external_id': 'uzex-auc-%s' % lot_id,
            'title': title[:500],
            'organization': None,
            'price': float(start_cost) if start_cost else None,
            'currency': 'UZS',
            'deadline': end_date[:10] if end_date else None,
            'date_end': end_date[:10] if end_date else None,
            'source': 'UZEX Обратные аукционы',
            'source_url': 'https://xarid.uzex.uz/auction/detail/%s' % lot_id,
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[UZEX-Auc] Transformed %d -> %d rows', len(all_items), len(rows))
    return rows


# ── Source: UZEX Prequalifications (GEO-BLOCKED from VPS) ────────

def fetch_and_transform_uzex_prequest():
    # type: () -> List[Dict[str, Any]]
    """Fetch UZEX prequalification lots — buyer requests for supplier qualification."""
    logger.info('[UZEX-Prq] Fetching prequalification lots...')
    all_items = []  # type: List[Dict[str, Any]]

    with httpx.Client(timeout=30) as client:
        for page in range(2):  # max 2 pages of 500
            skip = page * 500
            try:
                resp = client.post(
                    'https://xarid-api-prequest.uzex.uz/api/Public/GetLots',
                    json={'from': skip, 'to': skip + 500},
                    headers={
                        'Content-Type': 'application/json',
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get('Data', []) if isinstance(data, dict) else []
                if not items:
                    break
                all_items.extend(items)
                logger.info('[UZEX-Prq] Page %d: %d items (total fetched: %d)', page + 1, len(items), len(all_items))
                if len(items) < 500:
                    break
            except Exception as exc:
                logger.error('[UZEX-Prq] Error on page %d: %s', page + 1, str(exc))
                break

    if not all_items:
        logger.info('[UZEX-Prq] No items found')
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in all_items:
        lot_id = str(item.get('id', ''))
        if not lot_id:
            continue
        title = item.get('categoryName', '')
        if not title:
            continue

        org = item.get('customerName', '')
        start_cost = item.get('startCost')
        start_date = item.get('startDate', '') or ''
        end_date = item.get('endDate', '') or ''

        search_text = ' '.join(filter(None, [title, org])).lower()

        rows.append({
            'external_id': 'uzex-prq-%s' % lot_id,
            'title': title[:500],
            'organization': org[:200] if org else None,
            'price': float(start_cost) if start_cost else None,
            'currency': 'UZS',
            'deadline': end_date[:10] if end_date else None,
            'date_start': start_date[:10] if start_date else None,
            'date_end': end_date[:10] if end_date else None,
            'source': 'UZEX Предквалификации',
            'source_url': 'https://xarid.uzex.uz/prequalification/detail/%s' % lot_id,
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[UZEX-Prq] Transformed %d -> %d rows', len(all_items), len(rows))
    return rows


# ── DB & Alerts ──────────────────────────────────────────────────

def get_existing_ids(source_name):
    # type: (str) -> Set[str]
    """Get existing external_ids from Supabase for a source."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()

    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    existing = set()  # type: Set[str]
    offset = 0
    batch = 1000
    while True:
        resp = client.table('tenders').select('external_id').eq(
            'source', source_name
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
        except Exception as exc:
            logger.error('Upsert batch %d failed: %s', i, str(exc))

    return upserted


def _ai_check_relevance(title, organization, client):
    # type: (str, str, httpx.Client) -> bool
    """Check tender relevance via Qwen (OpenRouter). Returns True if relevant."""
    if not OPENROUTER_API_KEY:
        return True  # no key = skip filter

    prompt = (
        "Ты — эксперт по тендерам в сфере полиграфии и упаковки.\n\n"
        "Наша компания — типография и упаковочное производство в Узбекистане. "
        "Мы производим: коробки, этикетки, каталоги, книги, конверты, блокноты, "
        "календари, сувенирную продукцию, пакеты, папки.\n\n"
        "Оцени тендер — может ли наша компания реально на него подать заявку? "
        "Слово 'набор' может означать набор реагентов (НЕ наше), 'печать' — канцелярскую печать (НЕ наше), "
        "'пакет' — пакет документов (НЕ наше).\n\n"
        "Тендер:\nНазвание: %s\nЗаказчик: %s\n\n"
        "Ответь YES или NO.\n/no_think"
    ) % (title[:300], organization or "")

    try:
        resp = client.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': 'Bearer %s' % OPENROUTER_API_KEY},
            json={
                'model': AI_MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 20,
                'temperature': 0,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return True  # on error, let through

        import re
        answer = resp.json()['choices'][0]['message']['content'] or ''
        answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL).strip().upper()
        if not answer:
            return True
        is_relevant = answer.startswith('YES')
        if not is_relevant:
            logger.info('[AI Filter] REJECTED: %s (answer=%s)', title[:60], answer)
        return is_relevant
    except Exception as exc:
        logger.warning('[AI Filter] Error: %s', str(exc)[:80])
        return True


def send_alerts(new_rows, source_label):
    # type: (List[Dict[str, Any]], str) -> int
    """Send Telegram alerts for new tenders matching keywords."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    keywords = [k.strip().lower() for k in ALERT_KEYWORDS.split(',') if k.strip()]
    if not keywords:
        return 0

    MIN_PRICE = 10_000_000  # Минимальная сумма для алерта (10M сум)

    matching = []  # type: List[tuple]
    for row in new_rows:
        # Пропускаем тендеры с суммой меньше 10M
        price = row.get('price')
        if price is not None and price < MIN_PRICE:
            continue
        kw = _find_matching_keyword(row['title'], row.get('search_text', ''), keywords)
        if kw:
            matching.append((row, kw))

    if not matching:
        logger.info('[Alerts/%s] No matches (%d checked)', source_label, len(new_rows))
        return 0

    logger.info('[Alerts/%s] %d keyword matches, running AI filter...', source_label, len(matching))

    # AI relevance filter — reject false positives via Qwen
    if OPENROUTER_API_KEY:
        filtered = []  # type: List[tuple]
        with httpx.Client(timeout=15) as ai_client:
            for row, kw in matching:
                if _ai_check_relevance(row['title'], row.get('organization', ''), ai_client):
                    filtered.append((row, kw))
        rejected = len(matching) - len(filtered)
        if rejected:
            logger.info('[AI Filter] Passed %d / %d (rejected %d)', len(filtered), len(matching), rejected)
        matching = filtered
        if not matching:
            logger.info('[Alerts/%s] All rejected by AI filter', source_label)
            return 0

    bot_url = 'https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN
    sent = 0
    esc = lambda t: t.replace('*', '').replace('_', '').replace('`', '').replace('[', '')

    with httpx.Client(timeout=10) as client:
        for row, kw in matching:
            parts = ['*%s*' % esc(row['title'][:200])]
            if row.get('organization'):
                parts.append('Заказчик: %s' % esc(row['organization']))
            if row.get('price'):
                parts.append('Цена: {:,.0f} UZS'.format(row['price']))
            if row.get('deadline'):
                parts.append('Дедлайн: %s' % row['deadline'])
            parts.append('Источник: %s' % row['source'])
            parts.append('https://new.cooperation.uz')
            parts.append('#%s' % kw.replace(' ', '_'))

            try:
                resp = client.post(bot_url, json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': '\n'.join(parts),
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

    logger.info('[Alerts/%s] Sent %d / %d', source_label, sent, len(matching))
    return sent


# ── Process one source ───────────────────────────────────────────

def process_source(rows, source_name, label, dry_run=False):
    # type: (List[Dict[str, Any]], str, str, bool) -> Tuple[int, int, int]
    """Upsert rows and send alerts. Returns (upserted, new_count, alerts)."""
    if not rows:
        return 0, 0, 0

    if dry_run:
        logger.info('[%s] DRY RUN — %d rows', label, len(rows))
        for r in rows[:3]:
            logger.info('  %s | %s', r['title'][:60], r.get('organization') or '-')
        return 0, 0, 0

    # Find new rows
    existing_ids = get_existing_ids(source_name)
    new_rows = [r for r in rows if r['external_id'] not in existing_ids]
    logger.info('[%s] New: %d (existing: %d)', label, len(new_rows), len(existing_ids))

    # Upsert all
    upserted = upsert_to_supabase(rows)
    logger.info('[%s] Upserted: %d / %d', label, upserted, len(rows))

    # Alerts for new only
    alerts = 0
    if new_rows:
        alerts = send_alerts(new_rows, label)

    return upserted, len(new_rows), alerts


# ── Main ─────────────────────────────────────────────────────────

def main():
    # type: () -> None
    parser = argparse.ArgumentParser(description='Fetch cooperation.uz data')
    parser.add_argument('--dry-run', action='store_true', help='Fetch only, no DB')
    parser.add_argument('--pages', type=int, default=3, help='Max pages for plans (default: 3)')
    parser.add_argument('--source', choices=['plans', 'offers', 'lots', 'auction', 'eshop', 'uzex-auc', 'uzex-prq', 'all'],
                        default='all', help='Which source to fetch')
    args = parser.parse_args()

    logger.info('=== Cooperation.uz fetcher START (source=%s) ===', args.source)

    total_upserted = 0
    total_new = 0
    total_alerts = 0

    # 1. Plans
    if args.source in ('all', 'plans'):
        rows = fetch_and_transform_plans(max_pages=args.pages)
        u, n, a = process_source(rows, 'Cooperation.uz Закупочные планы', 'Plans', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a

    # 2. Offers (e-shop)
    if args.source in ('all', 'offers'):
        rows = fetch_and_transform_offers(max_pages=5)
        u, n, a = process_source(rows, 'Cooperation.uz Оферты', 'Offers', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a

    # 3. Lots (reverse tenders)
    if args.source in ('all', 'lots'):
        rows = fetch_and_transform_lots()
        u, n, a = process_source(rows, 'Cooperation.uz Лоты', 'Lots', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a

    # 4. Auction lots (buyer-initiated reverse auctions)
    if args.source in ('all', 'auction'):
        rows = fetch_and_transform_auction_lots()
        u, n, a = process_source(rows, 'Cooperation.uz Аукционы', 'AuctionLots', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a

    # 5. E-shop active lots (buyer selected product → trade started)
    if args.source in ('all', 'eshop'):
        rows = fetch_and_transform_eshop_lots()
        u, n, a = process_source(rows, 'Cooperation.uz Э-магазин лоты', 'EshopLots', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a

    # 6. UZEX reverse auctions (GEO-BLOCKED from VPS, runs on Mac)
    if args.source in ('all', 'uzex-auc'):
        rows = fetch_and_transform_uzex_auctions()
        u, n, a = process_source(rows, 'UZEX Обратные аукционы', 'UZEX-Auc', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a

    # 7. UZEX prequalifications (GEO-BLOCKED from VPS, runs on Mac)
    if args.source in ('all', 'uzex-prq'):
        rows = fetch_and_transform_uzex_prequest()
        u, n, a = process_source(rows, 'UZEX Предквалификации', 'UZEX-Prq', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a

    logger.info(
        '=== DONE: upserted %d, new %d, alerts %d ===',
        total_upserted, total_new, total_alerts,
    )


if __name__ == '__main__':
    main()
