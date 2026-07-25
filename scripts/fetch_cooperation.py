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
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

# Repo-root bootstrap: this file runs as a SCRIPT (sys.path[0] = scripts/), so the
# shared-pipeline import (`crawler.core.notifier` in send_alerts_unified) would fail
# without the repo root on the path. Must precede any `crawler.*` import.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

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
COMPETITOR_KEYWORDS = os.getenv('COMPETITOR_KEYWORDS', '')  # comma-separated company names
LEAD_GEN_ENABLED = os.getenv('LEAD_GEN_ENABLED', 'true').lower() in ('true', '1', 'yes')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Accept': 'application/json',
}

ALERT_KEYWORDS = (
    'упаковка,полиграфия,гофра,коробка,печать,этикетка,типография,'
    'книга,книж,каталог,брошюр,блокнот,календар,пакет,конверт,папка,'
    'ежедневник,сувенир,журнал,картон,подарочн,зонт,ручка,флешк,'
    'power bank,набор,плакат,постер,стенд,вывеск,'
    'бланк,наклейк,самоклей,тетрад,открытк,визитк,листовк,буклет,'
    'ярлык,стикер,бирк,'
    'quti,qog,qogoz,yorliq,daftar,karton,etiketka,konvert,kitob,blank,broshyur,katalog,'
    'қути,қоғоз,ёрлиқ,дафтар,китоб,'
    'packaging,printing,cardboard,label,box,qadoqlash,qadoq,bosma'
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


def _escape_md(text):
    # type: (str) -> str
    """Escape Markdown special characters (removes them to avoid injection)."""
    for ch in ('*', '_', '`', '[', ']'):
        text = text.replace(ch, '')
    return text


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


# Phase 2 ТНВЭД prefilter: machine-precise printing/packaging gate.
# Validated on 1719 live lots: +10 recall (Блакнот typo, Баннер, books), -5 hygiene FP, -47 reagent noise.
_TNVED_INCLUDE = ('4811', '4817', '4819', '4820', '4821', '4901', '4909', '4910', '4911')
_TNVED_EXCLUDE = ('4818', '4803', '3822')  # hygiene paper (toilet/napkins) + lab reagents


def _extract_tnved(search_text):
    # type: (str) -> str
    """Lots append the 10-digit ТНВЭД to search_text ('<product> <tnved>'). Take the last run."""
    import re
    matches = re.findall(r'\d{10}', search_text or '')
    return matches[-1] if matches else ''


def _classify_tnved(search_text):
    # type: (str) -> str
    """'exclude' (hygiene/reagents) | 'include' (printing) | 'other' | 'none'."""
    t = _extract_tnved(search_text)
    if not t:
        return 'none'
    if t.startswith(_TNVED_EXCLUDE):
        return 'exclude'
    if t.startswith(_TNVED_INCLUDE):
        return 'include'
    return 'other'


_ENKT_INCLUDE = ('17.21', '17.23', '17.29', '18.12')  # boxes/corrugated, stationery, other paper, printed matter
_ENKT_EXCLUDE = ('17.22',)  # sanitary paper (toilet/napkins)


def _classify_enkt(search_text):
    # type: (str) -> str
    """ЕНКТ (NN.NN.NN.NNN, e.g. 17.23.13.191): 'exclude'|'include'|'other'|'none'.
    eshop/auction expose enktCode; appended to search_text in their transforms."""
    import re
    m = re.search(r'(\d{2}\.\d{2})\.\d', search_text or '')
    if not m:
        return 'none'
    sec = m.group(1)
    if sec in _ENKT_EXCLUDE:
        return 'exclude'
    if sec in _ENKT_INCLUDE:
        return 'include'
    return 'other'


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
            'external_id': 'coop-plan-%s' % item_id,
            'title': title[:500],
            'organization': org[:200] if org else None,
            'price': None,
            'currency': 'UZS',
            'deadline': deadline,
            'source': 'Cooperation.uz Закупочные планы',
            'source_url': 'https://new.cooperation.uz/supplier/plans?planId=%s' % item_id,
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
            'source_url': 'https://new.cooperation.uz/supplier/offers?offerId=%s' % offer_num,
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
            'source_url': 'https://new.cooperation.uz/supplier/lots?lotId=%s' % lot_num,
            'status': 'active',
            'search_text': search_text[:1000],
            'extra_info': {k: v for k, v in [('quantity', item.get('quantity')), ('measure', item.get('measureName')), ('tnved', tnved), ('min_part', item.get('minPart')), ('max_part', item.get('maxPart')), ('certificate', item.get('isCertificate')), ('offer', item.get('offerNumber'))] if v is not None and v != ''},
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
        enkt = str(item.get('enktCode', '') or '')
        start_price = item.get('startPrice')
        current_price = item.get('price')
        providers = item.get('providerCount', 0)
        end_date = item.get('endDate', '') or ''
        begin_date = item.get('beginDate', '') or ''

        price_val = current_price or start_price
        search_text = ' '.join(filter(None, [title, org, region, enkt])).lower()

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
            'source_url': 'https://new.cooperation.uz/supplier/auction/%s' % lot_num,
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
            'source_url': 'https://new.cooperation.uz/supplier/eshop/%s' % lot_num,
            'status': 'active',
            'search_text': search_text[:1000],
            'collected_at': now,
        })

    logger.info('[EshopLots] Transformed %d -> %d rows', len(all_items), len(rows))
    return rows


# ── Source: UZEX Reverse Auctions (GEO-BLOCKED from VPS) ─────────

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


def get_alerted_ids(source_name):
    # type: (str) -> Set[str]
    """external_ids that were ALREADY alerted (alert_seq IS NOT NULL) for a source.
    Used to gate relevance alerts so active-but-never-alerted lots get re-evaluated
    each crawl instead of being permanently skipped for not being 'new to DB'."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()
    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    alerted = set()  # type: Set[str]
    offset = 0
    batch = 1000
    while True:
        resp = client.table('tenders').select('external_id').eq(
            'source', source_name
        ).not_.is_('alert_seq', 'null').range(offset, offset + batch - 1).execute()
        rows = resp.data or []
        for r in rows:
            alerted.add(r['external_id'])
        if len(rows) < batch:
            break
        offset += batch
    return alerted


# ── Source: Auction Contracts (NEW 2026-05-22 — replaces blocked E-IMZO path) ──

def fetch_and_transform_auction_contracts(max_pages=10, page_size=500):
    # type: (int, int) -> List[Dict[str, Any]]
    """Fetch CLOSED auction contracts from stat-new.cooperation.uz — buyer + winner + prices.

    This replaces the E-IMZO blocker for organization enrichment. Previously
    cooperation.uz/cabinet GetLotInfo required E-IMZO auth → blocked. The new
    stat-new portal (launched ~2026-04) exposes /gateway/api-stat/auction-contracts
    publicly with full customer/producer/prices/regions.

    Response: {code, total, content: [{contractNumber, lotNumber, products, customerName,
               customerTin, producerName, producerTin, beginDate, endDate, dealTime,
               startPrice, contractAmount, price, amount, offerCount,
               customerRegionId, customerDistrictId, producerRegionId, producerDistrictId,
               statusId}]}
    """
    logger.info('[AucContracts] Fetching closed auction contracts...')
    all_items = []  # type: List[Dict[str, Any]]
    total = 0

    with httpx.Client(timeout=30) as client:
        for page in range(max_pages):
            try:
                resp = client.get(
                    'https://stat-new.cooperation.uz/gateway/api-stat/auction-contracts',
                    params={'skip': page * page_size, 'take': page_size},
                    headers={**HEADERS, 'Referer': 'https://stat-new.cooperation.uz/'},
                )
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    break
                if page == 0:
                    total = int(data.get('total') or 0)
                    logger.info('[AucContracts] Total available: %d', total)
                items = data.get('content', []) or []
                if not items:
                    break
                all_items.extend(items)
                if len(all_items) >= total:
                    break
            except Exception as exc:
                logger.error('[AucContracts] Page %d error: %s', page, str(exc))
                break

    logger.info('[AucContracts] Got %d items', len(all_items))
    if not all_items:
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in all_items:
        contract_no = str(item.get('contractNumber') or '').strip()
        lot_no = str(item.get('lotNumber') or '').strip()
        # Prefer contractNumber for ID (unique per contract); fall back to lotNumber.
        ext_key = contract_no or lot_no
        if not ext_key:
            continue

        # Title: join up to 3 product names
        products = item.get('products') or []
        product_names = []
        for p in products[:5]:
            name = _extract_ru((p or {}).get('name', ''))
            if name:
                product_names.append(name)
        title = ', '.join(product_names) if product_names else 'Контракт %s' % ext_key
        if len(title) > 500:
            title = title[:497] + '...'

        org = _extract_ru(item.get('customerName', '')) or None
        producer = _extract_ru(item.get('producerName', '')) or None

        contract_amount = item.get('contractAmount')
        start_price = item.get('startPrice')
        # contractAmount = final agreed total. startPrice = budget. Use contractAmount.
        price_val = contract_amount or start_price

        deal_time = item.get('dealTime') or ''
        begin_date = item.get('beginDate') or ''
        end_date = item.get('endDate') or ''

        # extra_info: producer + TINs + regions + offer count
        extra_info = {}
        if producer:
            extra_info['Победитель'] = producer[:120]
        customer_tin = item.get('customerTin')
        if customer_tin:
            extra_info['ИНН заказчика'] = str(customer_tin)
        producer_tin = item.get('producerTin')
        if producer_tin:
            extra_info['ИНН победителя'] = str(producer_tin)
        offer_count = item.get('offerCount')
        if offer_count is not None:
            extra_info['Кол-во оферт'] = str(offer_count)
        # Persist the source lotNumber — it's parsed but was thrown away, breaking
        # the offer→lot→contract chain (gold-map 2026-07-05). Keeping it lets the
        # weekly routine join a won contract back to the lot we alerted.
        if lot_no:
            extra_info['Лот'] = lot_no
        if start_price and contract_amount and start_price > 0:
            try:
                discount_pct = round(100 * (1 - float(contract_amount) / float(start_price)), 1)
                if discount_pct > 0:
                    extra_info['Скидка'] = '%s%%' % discount_pct
            except Exception:
                pass

        search_text = ' '.join(filter(None, [title, org or '', producer or ''])).lower()

        rows.append({
            'external_id': 'coop-contract-%s' % ext_key,
            'title': title,
            'organization': org[:200] if org else None,
            'price': float(price_val) if price_val else None,
            'currency': 'UZS',
            'deadline': end_date[:10] if end_date else None,
            'date_start': begin_date[:10] if begin_date else None,
            'date_end': deal_time[:10] if deal_time else (end_date[:10] if end_date else None),
            'source': 'Cooperation.uz Контракты',
            # 2026-05-22: stat-new portal launched; no per-contract deep-link yet
            # discovered. Use registry page so user can search by contractNumber.
            'source_url': 'https://stat-new.cooperation.uz/all-deals',
            'status': 'closed',  # contracts are completed deals
            'message_type': 'info',
            'search_text': search_text[:1000],
            'extra_info': extra_info,
            'collected_at': now,
        })

    logger.info('[AucContracts] Transformed %d -> %d rows', len(all_items), len(rows))
    return rows


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


_REJECT_TITLES = [
    "книги печатные",
    "подписке и доставке периодического печатного издания",
    "подписке и доставке периодических печатных изданий",
    "марля полиграфическая",
    "nfc визитк",
]


_OFFER_CACHE = {}


def _fetch_offer_detail(product_name, offer_number):
    # type: (str, str) -> Optional[dict]
    """E-catalog offer lookup (GetAllOffer OfferType=1, productName search) -> unitPrice + photo.
    Joins a lot to its supplier offer (the priced catalog listing the lot references)."""
    if not offer_number:
        return None
    if offer_number in _OFFER_CACHE:
        return _OFFER_CACHE[offer_number]
    result = None
    try:
        q = ' '.join((product_name or '').split()[:3])
        with httpx.Client(timeout=20) as client:
            r = client.get('https://new.cooperation.uz/ocelot/api-client/Client/GetAllOffer',
                           params={'OfferType': 1, 'skip': 0, 'take': 50, 'productName': q}, headers=HEADERS)
            if r.status_code == 200:
                for o in (r.json().get('result') or {}).get('data') or []:
                    if o.get('offerNumber') == offer_number:
                        photo = (o.get('photos') or '').split('|')[0]
                        result = {'unit_price': o.get('unitPrice'),
                                  'photo': ('https://new.cooperation.uz/ocelot/' + photo) if photo else None}
                        # Reference supplier — the competitor whose catalog card the
                        # buyer anchored this lot to. Их цену и надо перебивать
                        # (Brayl-darslik case 2026-07-05: DIZAYN-PRINT MCHJ).
                        comp = o.get('company') or {}
                        cname = comp.get('name')
                        if isinstance(cname, dict):
                            cname = cname.get('ru') or cname.get('uz') or ''
                        if cname:
                            result['ref_supplier'] = str(cname)[:80]
                        if comp.get('tin'):
                            result['ref_supplier_tin'] = str(comp['tin'])
                        break
    except Exception as exc:
        logger.warning('[OfferEnrich] %s: %s', offer_number, str(exc)[:80])
    _OFFER_CACHE[offer_number] = result
    return result


def _enrich_lot_row(row):
    # type: (Dict[str, Any]) -> None
    """Enrich a 'Cooperation.uz Лоты' row in place: offer price + photo + reference
    supplier via e-catalog offerNumber join, computed total (unit_price × quantity),
    and DB persist (upsert ran BEFORE alerts — without the persist the Vercel card
    never sees price/photo, the 2026-07-05 empty-card root cause). No-op for other
    sources / already-enriched rows. Extracted from the legacy send loop (2026-07-22)
    so both legacy and unified paths share it."""
    _lei = row.get('extra_info') or {}
    if row.get('source') != 'Cooperation.uz Лоты' or not _lei.get('offer') or _lei.get('unit_price'):
        return
    _od = _fetch_offer_detail(row.get('title', ''), _lei['offer'])
    if _od:
        _lei.update({k: v for k, v in _od.items() if v is not None})
        row['extra_info'] = _lei
    try:
        if not row.get('price') and _lei.get('unit_price') and _lei.get('quantity'):
            row['price'] = float(_lei['unit_price']) * float(_lei['quantity'])
    except (TypeError, ValueError):
        pass
    try:
        from supabase import create_client as _cc
        _cc(SUPABASE_URL, SUPABASE_KEY).table('tenders').update(
            {'extra_info': _lei, 'price': row.get('price')}
        ).eq('external_id', row.get('external_id', '')).eq('source', row['source']).execute()
    except Exception as _pexc:
        logger.warning('[OfferEnrich] persist failed: %s', str(_pexc)[:80])


def _row_to_raw_tender(row):
    # type: (Dict[str, Any]) -> Any
    """Coop dict row → RawTender for the shared notifier pipeline.

    Traps handled: DB jsonb extra_info keeps native types (int quantity, bool
    certificate) while RawTender wants Dict[str, str] — str-coerce like
    crawler/scripts/investigator.py (fix af1c155); organization can be None;
    contracts carry message_type='info' — preserved AS IS so the shared ALERT_TYPES
    stage drops them deliberately (closed deals are not alerts; #конкурент covers
    them). extra_info['tnved'] falls back to search_text so the notifier's
    tnved-scope hook keeps the include channel (100 alerts/30d) alive."""
    from crawler.core.models import RawTender
    ei = row.get('extra_info')
    ei = {str(k): ('' if v is None else str(v)) for k, v in ei.items()} if isinstance(ei, dict) else {}
    st = row.get('search_text') or ''
    if 'tnved' not in ei:
        _tn = _extract_tnved(st)
        if _tn:
            ei['tnved'] = _tn
    ext = row.get('external_id') or ''
    return RawTender(
        id=ext, external_id=ext,
        title=row.get('title') or '',
        organization=row.get('organization') or '',
        price=row.get('price'), currency=row.get('currency') or 'UZS',
        deadline=row.get('deadline'),
        date_start=row.get('date_start'), date_end=row.get('date_end'),
        source=row.get('source') or '', source_url=row.get('source_url') or '',
        status=row.get('status') or 'active', search_text=st,
        message_type=row.get('message_type') or 'tender',
        extra_info=ei,
        **({'collected_at': row['collected_at']} if row.get('collected_at') else {})
    )


def _prefilter_rows(rows, source_label):
    # type: (List[Dict[str, Any]], str) -> List[Dict[str, Any]]
    """Producer-side cheap gates that cut shared-pipeline AI spend. DROPS ONLY, never
    force-passes (the legacy tnved/enkt include force-pass is replaced by the notifier
    tnved_scope mechanism). _REJECT_TITLES kept whole incl. «книги печатные»: coop
    rows carry real product names, not the UZEX OKED category the notifier dropped it for."""
    kept = []
    n_scope = n_title = 0
    for row in rows:
        st = row.get('search_text', '')
        if _classify_tnved(st) == 'exclude' or _classify_enkt(st) == 'exclude':
            n_scope += 1
            continue
        title_low = (row.get('title') or '').lower()
        if any(rj in title_low for rj in _REJECT_TITLES):
            n_title += 1
            continue
        kept.append(row)
    if n_scope or n_title:
        logger.info('[CoopPrefilter/%s] dropped %d (tnved/enkt-exclude=%d, reject-title=%d)',
                    source_label, n_scope + n_title, n_scope, n_title)
    return kept


def _notify_pipeline_failure(source_label, n_rows, exc):
    # type: (str, int, Exception) -> None
    """Operational alert (NOT a tender alert) when the shared pipeline throws.
    The legacy in-file sender was deleted after the 48h soak (2026-07-25), so there
    is no second delivery path by design: a fallback that bypasses mutes/verifier is
    exactly the noise class this unification removed. But a silent failure is how
    alerts died unnoticed for weeks before — so make it visible in Telegram, not
    only in a log nobody greps."""
    try:
        with httpx.Client(timeout=10, trust_env=False) as c:
            c.post('https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN, json={
                'chat_id': TELEGRAM_CHAT_ID,
                'text': ('⚠️ Coop-алерты не отправлены (%s): пайплайн упал.\n'
                         '%d лотов не разосланы. Ошибка: %s\n'
                         'Лоты/Аукционы повторятся сами (gate_on_alerted), остальное — потеряно за этот прогон.'
                         % (source_label, n_rows, str(exc)[:200])),
                'disable_web_page_preview': True,
            })
    except Exception as _texc:
        logger.error('[Unified] failure-notify also failed: %s', str(_texc)[:80])


def send_alerts_unified(new_rows, source_label):
    # type: (List[Dict[str, Any]], str) -> int
    """Route coop tender alerts through the SHARED notifier pipeline (unification
    2026-07-22): mute compliance, 3-tier routing, playbook-aware AI, verifier,
    digest, screenshots. Single delivery path by design — see
    _notify_pipeline_failure for why there is no legacy fallback. Lazy import keeps
    module import (and the tests) independent of the crawler package."""
    rows = _prefilter_rows(new_rows, source_label)
    if not rows:
        return 0
    if source_label == 'Lots':
        keywords = [k.strip().lower() for k in ALERT_KEYWORDS.split(',') if k.strip()]
        for r in rows:
            # keyword-gate the enrichment HTTP cost, as the legacy loop implicitly did
            if _find_matching_keyword(r.get('title', ''), r.get('search_text', ''), keywords):
                _enrich_lot_row(r)
    try:
        import asyncio
        from crawler.core.notifier import send_alerts as _shared_send
        tenders = [_row_to_raw_tender(r) for r in rows]
        return asyncio.run(_shared_send(tenders))
    except Exception as exc:
        logger.error('[Unified] shared pipeline FAILED for %s (%d rows): %s',
                     source_label, len(rows), str(exc)[:200])
        _notify_pipeline_failure(source_label, len(rows), exc)
        return 0


def send_competitor_alerts(new_rows, source_label):
    # type: (List[Dict[str, Any]], str) -> int
    """Send Telegram alerts when a competitor company is detected in new rows."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0
    if not COMPETITOR_KEYWORDS:
        return 0

    comp_keywords = [k.strip().lower() for k in COMPETITOR_KEYWORDS.split(',') if k.strip()]
    if not comp_keywords:
        return 0

    bot_url = 'https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN
    sent = 0

    with httpx.Client(timeout=10, trust_env=False) as client:
        for row in new_rows:
            org = (row.get('organization') or '').lower()
            if not org:
                continue
            matched_comp = None  # type: Optional[str]
            for ck in comp_keywords:
                if ck in org:
                    matched_comp = ck
                    break
            if not matched_comp:
                continue

            parts = [
                '🏭 Конкурент: %s' % _escape_md(row.get('organization', '')[:200]),
                _escape_md(row['title'][:200]),
            ]
            if row.get('price'):
                parts.append('Цена: {:,.0f} UZS'.format(row['price']))
            parts.append('Источник: %s' % row.get('source', ''))
            parts.append('#конкурент')

            try:
                resp = client.post(bot_url, json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': '\n'.join(parts),
                    'disable_web_page_preview': True,
                    'protect_content': True,
                })
                if resp.status_code == 200:
                    sent += 1
                else:
                    logger.warning('[Competitor] Telegram %d: %s', resp.status_code, resp.text[:200])
            except Exception as exc:
                logger.warning('[Competitor] Error: %s', str(exc))

    if sent:
        logger.info('[Competitor/%s] Sent %d alerts', source_label, sent)
    return sent


def send_lead_alerts(new_rows, source_label):
    # type: (List[Dict[str, Any]], str) -> int
    """Send Telegram alerts for lead generation — plans matching our keywords."""
    if not LEAD_GEN_ENABLED:
        return 0
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    keywords = [k.strip().lower() for k in ALERT_KEYWORDS.split(',') if k.strip()]
    if not keywords:
        return 0

    bot_url = 'https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN
    sent = 0

    with httpx.Client(timeout=10, trust_env=False) as client:
        for row in new_rows:
            org = row.get('organization') or ''
            if not org:
                continue
            kw = _find_matching_keyword(row['title'], row.get('search_text', ''), keywords)
            if not kw:
                continue

            # Extract month/year from deadline (format: "month/year")
            deadline = row.get('deadline') or ''
            month_year = deadline if '/' in deadline else ''

            parts = [
                '📋 Лид: %s планирует закупку' % _escape_md(org[:200]),
                _escape_md(row['title'][:200]),
            ]
            if month_year:
                parts.append('Месяц: %s' % month_year)
            parts.append('Источник: %s' % row.get('source', ''))
            parts.append('#лид')

            try:
                resp = client.post(bot_url, json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': '\n'.join(parts),
                    'disable_web_page_preview': True,
                    'protect_content': True,
                })
                if resp.status_code == 200:
                    sent += 1
                else:
                    logger.warning('[Lead] Telegram %d: %s', resp.status_code, resp.text[:200])
            except Exception as exc:
                logger.warning('[Lead] Error: %s', str(exc))

    if sent:
        logger.info('[Lead/%s] Sent %d alerts', source_label, sent)
    return sent


# ── Process one source ───────────────────────────────────────────

def process_source(rows, source_name, label, dry_run=False, is_plans=False, gate_on_alerted=False,
                   tender_alerts=True):
    # type: (List[Dict[str, Any]], str, str, bool, bool, bool, bool) -> Tuple[int, int, int, int, int]
    """Upsert rows and send alerts. Returns (upserted, new_count, alerts, competitor_alerts, lead_alerts).
    tender_alerts=False keeps upsert + competitor/lead intel but skips generic tender
    alerting (plans: runner's cooperation-plans-filtered owns those alerts — T0-a)."""
    if not rows:
        return 0, 0, 0, 0, 0

    if dry_run:
        logger.info('[%s] DRY RUN — %d rows', label, len(rows))
        for r in rows[:3]:
            logger.info('  %s | %s', r['title'][:60], r.get('organization') or '-')
        return 0, 0, 0, 0, 0

    # Find new rows (brand-new to DB) — used for competitor/lead intel as before
    existing_ids = get_existing_ids(source_name)
    new_rows = [r for r in rows if r['external_id'] not in existing_ids]
    logger.info('[%s] New: %d (existing: %d)', label, len(new_rows), len(existing_ids))

    # Relevance-alert gate: optionally re-surface active lots never alerted (alert_seq NULL).
    # Fixes the leak where a relevant lot upserted during an outage / before keyword expansion
    # became 'existing' and was permanently skipped. GetLotsInTrade returns only active lots,
    # so this safely recovers active candidates; dedup is implicit (alerted rows are excluded).
    if gate_on_alerted:
        alerted_ids = get_alerted_ids(source_name)
        alert_rows = [r for r in rows if r['external_id'] not in alerted_ids]
        logger.info('[%s] Alert-gate(not-yet-alerted): %d (already alerted: %d)', label, len(alert_rows), len(alerted_ids))
    else:
        alert_rows = new_rows

    # Upsert all
    upserted = upsert_to_supabase(rows)
    logger.info('[%s] Upserted: %d / %d', label, upserted, len(rows))

    alerts = 0
    comp_alerts = 0
    lead_alerts = 0
    if alert_rows and tender_alerts:
        alerts = send_alerts_unified(alert_rows, label)
    # Competitor + lead intel stay gated on brand-new rows (no backfill burst)
    if new_rows:
        comp_alerts = send_competitor_alerts(new_rows, label)
        if is_plans:
            lead_alerts = send_lead_alerts(new_rows, label)

    return upserted, len(alert_rows), alerts, comp_alerts, lead_alerts


# ── Main ─────────────────────────────────────────────────────────

def main():
    # type: () -> None
    parser = argparse.ArgumentParser(description='Fetch cooperation.uz data')
    parser.add_argument('--dry-run', action='store_true', help='Fetch only, no DB')
    parser.add_argument('--pages', type=int, default=3, help='Max pages for plans (default: 3)')
    parser.add_argument('--source', choices=['plans', 'offers', 'lots', 'auction', 'eshop', 'contracts', 'all'],
                        default='all', help='Which source to fetch')
    args = parser.parse_args()

    logger.info('=== Cooperation.uz fetcher START (source=%s) ===', args.source)

    total_upserted = 0
    total_new = 0
    total_alerts = 0
    total_competitor = 0
    total_leads = 0

    # 1. Plans
    if args.source in ('all', 'plans'):
        rows = fetch_and_transform_plans(max_pages=args.pages)
        # tender_alerts=False (T0-a, 2026-07-22): the runner's cooperation-plans-filtered
        # earns the plan alerts (18/30d vs 0 here); this path keeps upsert + #лид only.
        u, n, a, c, l = process_source(rows, 'Cooperation.uz Закупочные планы', 'Plans', args.dry_run,
                                       is_plans=True, tender_alerts=False)
        total_upserted += u
        total_new += n
        total_alerts += a
        total_competitor += c
        total_leads += l

    # 2. Offers (e-shop)
    if args.source in ('all', 'offers'):
        rows = fetch_and_transform_offers(max_pages=5)
        u, n, a, c, l = process_source(rows, 'Cooperation.uz Оферты', 'Offers', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a
        total_competitor += c
        total_leads += l

    # 3. Lots (reverse tenders)
    if args.source in ('all', 'lots'):
        rows = fetch_and_transform_lots()
        u, n, a, c, l = process_source(rows, 'Cooperation.uz Лоты', 'Lots', args.dry_run, gate_on_alerted=True)
        total_upserted += u
        total_new += n
        total_alerts += a
        total_competitor += c
        total_leads += l

    # 4. Auction lots (buyer-initiated reverse auctions)
    if args.source in ('all', 'auction'):
        rows = fetch_and_transform_auction_lots()
        u, n, a, c, l = process_source(rows, 'Cooperation.uz Аукционы', 'AuctionLots', args.dry_run, gate_on_alerted=True)
        total_upserted += u
        total_new += n
        total_alerts += a
        total_competitor += c
        total_leads += l

    # 5. E-shop active lots (buyer selected product → trade started)
    if args.source in ('all', 'eshop'):
        rows = fetch_and_transform_eshop_lots()
        u, n, a, c, l = process_source(rows, 'Cooperation.uz Э-магазин лоты', 'EshopLots', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a
        total_competitor += c
        total_leads += l

    # 5b. Auction Contracts (NEW 2026-05-22: closed deals with customer/producer/prices
    # via public stat-new portal — replaces blocked E-IMZO path for organization data)
    if args.source in ('all', 'contracts'):
        rows = fetch_and_transform_auction_contracts(max_pages=10, page_size=500)
        u, n, a, c, l = process_source(rows, 'Cooperation.uz Контракты', 'AucContracts', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a
        total_competitor += c
        total_leads += l

    logger.info(
        '=== DONE: upserted %d, new %d, alerts %d, competitor %d, leads %d ===',
        total_upserted, total_new, total_alerts, total_competitor, total_leads,
    )


if __name__ == '__main__':
    main()
