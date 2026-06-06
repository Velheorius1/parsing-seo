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
AI_MODEL = os.getenv('AI_RELEVANCE_MODEL', 'qwen/qwen3.6-max-preview')
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
            # 2026-05-21: UZEX migrated xarid.uzex.uz → new-xarid.uzex.uz.
            # Universal URL pattern works for all UZEX lot types.
            'source_url': 'https://new-xarid.uzex.uz/home/shop/detail/%s?elektron=true' % lot_id,
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
            # 2026-05-21: UZEX migrated xarid.uzex.uz → new-xarid.uzex.uz.
            # Universal URL works for prequalifications (verified SO0068843, SO0068603).
            'source_url': 'https://new-xarid.uzex.uz/home/shop/detail/%s?elektron=true' % lot_id,
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


AI_MODEL_FAST = os.getenv('AI_RELEVANCE_MODEL_FAST', 'deepseek/deepseek-v4-flash')
AI_SCORE_THRESHOLD = int(os.getenv('AI_SCORE_THRESHOLD', '50'))
# Escalate a flash REJECT to the slow max model only when its score is within
# [floor, threshold) — a borderline call worth a recall-rescue second opinion.
# Confident rejects (score < floor) skip the slow call. Parse-fails always escalate.
_AI_ESCALATE_FLOOR = int(os.getenv('AI_ESCALATE_FLOOR', '30'))
_VALID_CATEGORIES = ('client', 'ad', 'irrelevant')

_AI_PROMPT = (
    "Ты — эксперт по тендерам в сфере полиграфии и упаковки.\n\n"
    "Наша компания — типография и упаковочное производство в Узбекистане. "
    "Мы производим: коробки, гофрокороб, этикетки, наклейки, каталоги, книги, брошюры, "
    "конверты, блокноты, ежедневники, бланки, календари, папки, бумажные пакеты.\n\n"
    "НЕ НАШЕ: набор реагентов (мед/лаб), канцелярская печать (штампы), пакет документов, "
    "наружная реклама (баннеры на фасадах, вывески, световые короба), вакансии/найм, "
    "IT/сайты/SMM, мебель/оборудование/станки, стройматериалы, туалетная бумага/салфетки, "
    "закупка готовых книг/тетрадей (не печать на заказ), реклама ЧУЖИХ услуг.\n\n"
    "Тендер:\nНазвание: %s\nЗаказчик: %s\n\n"
    "Ответь СТРОГО в JSON (только JSON, без markdown):\n"
    "{\"score\": <0-100>, \"category\": \"<client|ad|irrelevant>\", \"reason\": \"<до 100 символов>\"}\n\n"
    "score: 90-100 = точно наш заказ; 70-89 = вероятно наш; 40-69 = смежная; 0-39 = не наш.\n"
    "category: client = заказчик хочет купить полиграфию/упаковку; ad = реклама чужих услуг; "
    "irrelevant = не наша область.\n/no_think"
)


def _extract_json_object(text):
    # type: (str) -> Optional[dict]
    """First {...} JSON object, tolerating code fences and <think> blocks."""
    if not text:
        return None
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if not m:
            return None
        candidate = m.group(0)
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None


def _parse_relevance(payload):
    # type: (dict) -> Optional[dict]
    """Validate AI JSON -> {is_relevant, score, category, reason}. None on bad shape."""
    try:
        score = int(payload.get('score'))
    except (TypeError, ValueError):
        return None
    score = max(0, min(100, score))
    cat = str(payload.get('category', '') or '').strip().lower()
    if cat not in _VALID_CATEGORIES:
        cat = 'client' if score >= AI_SCORE_THRESHOLD else 'irrelevant'
    reason = str(payload.get('reason') or '')[:200].strip()
    return {
        'is_relevant': score >= AI_SCORE_THRESHOLD,
        'score': score, 'category': cat, 'reason': reason,
    }


def _ai_json_call(title, organization, client, model):
    # type: (str, str, httpx.Client, str) -> Optional[dict]
    """One JSON relevance call. Returns parsed dict or None on failure."""
    prompt = _AI_PROMPT % (title[:300], organization or '')
    try:
        resp = client.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': 'Bearer %s' % OPENROUTER_API_KEY},
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 400,
                'temperature': 0,
                'response_format': {'type': 'json_object'},
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        content = resp.json()['choices'][0]['message']['content'] or ''
        payload = _extract_json_object(content)
        return _parse_relevance(payload) if payload else None
    except Exception as exc:
        logger.warning('[AI] %s error: %s', model, str(exc)[:80])
        return None


def _ai_check_relevance(title, organization, client):
    # type: (str, str, httpx.Client) -> dict
    """Relevance via DeepSeek JSON. Hybrid: fast (flash) -> max (pro) second opinion on
    parse-fail or reject (recall rescue). Returns {is_relevant, score, category, reason}."""
    if not OPENROUTER_API_KEY:
        return {'is_relevant': True, 'score': None, 'category': None, 'reason': None}
    res = _ai_json_call(title, organization, client, AI_MODEL_FAST)
    # Escalate to the slow max model only on parse-fail or a BORDERLINE reject
    # (recall rescue). A confident low score doesn't need a slow second opinion.
    escalate = res is None or (
        not res['is_relevant'] and res.get('score') is not None
        and res['score'] >= _AI_ESCALATE_FLOOR
    )
    if escalate:
        max_res = _ai_json_call(title, organization, client, AI_MODEL)
        if max_res is not None:
            res = max_res
    if res is None:
        return {'is_relevant': True, 'score': None, 'category': None, 'reason': 'AI parse failed (fail-open)'}
    if not res['is_relevant']:
        logger.info('[AI Filter] REJECTED: %s (score=%s)', title[:60], res['score'])
    return res


_SB_CLIENT = None


def _get_supabase():
    # type: () -> Any
    """Cached Supabase client. Was referenced by 5 callers but never defined,
    so _save_alert_seq / _lookup_tender_uuid / _get_next_alert_seq silently no-op'd
    (NameError caught by their try/except). Defining it restores them."""
    global _SB_CLIENT
    if _SB_CLIENT is None:
        from supabase import create_client
        _SB_CLIENT = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _SB_CLIENT


def _get_next_alert_seq(count=1):
    """Reserve sequential alert numbers from Supabase."""
    try:
        sb = _get_supabase()
        result = sb.rpc("get_next_alert_seq", {"p_count": count}).execute()
        if result.data is not None:
            return int(result.data)
    except Exception:
        pass
    try:
        sb = _get_supabase()
        result = sb.table("tenders").select("alert_seq").not_.is_("alert_seq", "null").order("alert_seq", desc=True).limit(1).execute()
        if result.data:
            return int(result.data[0]["alert_seq"]) + 1
    except Exception:
        pass
    return 9999


def _save_alert_seq(external_id, source, alert_seq, telegram_message_id=None):
    """Save alert_seq to tenders table."""
    try:
        sb = _get_supabase()
        update = {"alert_seq": alert_seq}
        if telegram_message_id is not None:
            update["telegram_message_id"] = telegram_message_id
        sb.table("tenders").update(update).eq("external_id", external_id).eq("source", source).execute()
    except Exception as exc:
        logger.warning("[Feedback] Failed to save alert_seq %d: %s", alert_seq, str(exc)[:80])


def _persist_relevance(external_id, source, score, category, reason):
    # type: (str, str, int, str, str) -> None
    """Persist AI relevance decision to the tender row (analytics + feedback substrate).

    Phase 1: binary score (passed=70/client, rejected=20/irrelevant). A real 0-100 score
    arrives in Phase 2 with the TNVED/ENKT pipeline. Reuses existing columns (migration 017).
    """
    if not external_id:
        return
    try:
        sb = _get_supabase()
        sb.table("tenders").update({
            "relevance_score": score,
            "relevance_category": category,
            "relevance_reason": (reason or "")[:500],
        }).eq("external_id", external_id).eq("source", source).execute()
    except Exception as exc:
        logger.warning("[Persist] relevance_score fail %s: %s", external_id, str(exc)[:80])


def _lookup_tender_uuid(external_id, source):
    """Look up Supabase UUID for detail page link."""
    try:
        sb = _get_supabase()
        result = sb.table("tenders").select("id").eq("external_id", external_id).eq("source", source).limit(1).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception:
        pass
    return None


_DETAIL_PAGE = "https://parsing-seo.vercel.app/tenders"

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
                        break
    except Exception as exc:
        logger.warning('[OfferEnrich] %s: %s', offer_number, str(exc)[:80])
    _OFFER_CACHE[offer_number] = result
    return result


def send_alerts(new_rows, source_label):
    # type: (List[Dict[str, Any]], str) -> int
    """Send Telegram alerts for new tenders matching keywords.

    Unified version: numbered alerts, inline buttons, detail page URL, fast reject filter.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    keywords = [k.strip().lower() for k in ALERT_KEYWORDS.split(',') if k.strip()]
    if not keywords:
        return 0

    MIN_PRICE = 10_000_000

    matching = []  # type: List[tuple]
    for row in new_rows:
        price = row.get('price')
        if price is not None and price < MIN_PRICE:
            continue
        st = row.get('search_text', '')
        tcls = _classify_tnved(st)
        ecls = _classify_enkt(st)
        if tcls == 'exclude' or ecls == 'exclude':
            continue  # hygiene paper / lab reagents — reject even if keyword matched
        kw = _find_matching_keyword(row['title'], st, keywords)
        if kw or tcls == 'include' or ecls == 'include':
            label = kw or (('tnved' + _extract_tnved(st)[:4]) if tcls == 'include' else 'enkt')
            matching.append((row, label))

    if not matching:
        logger.info('[Alerts/%s] No matches (%d checked)', source_label, len(new_rows))
        return 0

    # Fast reject filter — remove obvious non-relevant items
    before = len(matching)
    matching = [(r, kw) for r, kw in matching if not any(rej in r['title'].lower() for rej in _REJECT_TITLES)]
    rejected_fast = before - len(matching)
    if rejected_fast:
        logger.info('[Fast Reject/%s] Removed %d non-relevant by title', source_label, rejected_fast)
    if not matching:
        logger.info('[Alerts/%s] All rejected by fast filter', source_label)
        return 0

    logger.info('[Alerts/%s] %d keyword matches, running AI filter...', source_label, len(matching))

    # AI relevance filter
    if OPENROUTER_API_KEY:
        filtered = []  # type: List[tuple]
        with httpx.Client(timeout=20, trust_env=False) as ai_client:
            for row, kw in matching:
                res = _ai_check_relevance(row['title'], row.get('organization', ''), ai_client)
                _persist_relevance(
                    row.get('external_id', ''), row['source'],
                    res['score'] if res.get('score') is not None else (70 if res['is_relevant'] else 20),
                    res.get('category') or ('client' if res['is_relevant'] else 'irrelevant'),
                    (res.get('reason') or 'AI') + ' (kw=' + kw + ')',
                )
                if res['is_relevant']:
                    filtered.append((row, kw))
        rejected = len(matching) - len(filtered)
        if rejected:
            logger.info('[AI Filter] Passed %d / %d (rejected %d)', len(filtered), len(matching), rejected)
        matching = filtered
        if not matching:
            logger.info('[Alerts/%s] All rejected by AI filter', source_label)
            return 0

    # Reserve alert sequence numbers
    start_seq = _get_next_alert_seq(len(matching))

    bot_url = 'https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN
    sent = 0

    with httpx.Client(timeout=10, trust_env=False) as client:
        for i, (row, kw) in enumerate(matching):
            seq = start_seq + i
            parts = []

            # Enrich lot with offer price + photo (e-catalog join via offerNumber)
            _lei = row.get('extra_info') or {}
            if row.get('source') == 'Cooperation.uz Лоты' and _lei.get('offer') and not _lei.get('unit_price'):
                _od = _fetch_offer_detail(row.get('title', ''), _lei['offer'])
                if _od:
                    _lei.update(_od)
                    row['extra_info'] = _lei

            # Header with alert number
            msg_type = row.get('message_type', 'tender')
            if msg_type == 'customer_request':
                parts.append('#%03d [ЗАПРОС КЛИЕНТА]' % seq)
            else:
                parts.append('#%03d [ТЕНДЕР]' % seq)

            parts.append('*%s*' % _escape_md(row['title'][:200]))
            if row.get('organization'):
                parts.append('Заказчик: %s' % _escape_md(row['organization']))
            if row.get('price'):
                parts.append('Цена: {:,.0f} UZS'.format(row['price']))
            if row.get('deadline'):
                parts.append('Дедлайн: %s' % row['deadline'])
            _ei = row.get('extra_info') or {}
            if _ei.get('quantity'):
                parts.append('Кол-во: %s %s' % (_ei['quantity'], _ei.get('measure') or ''))
            if _ei.get('min_part') or _ei.get('max_part'):
                parts.append('Партия: %s-%s' % (_ei.get('min_part',''), _ei.get('max_part','')))
            if _ei.get('certificate'):
                parts.append('Сертификат: требуется')
            if _ei.get('unit_price'):
                parts.append('Цена: %s сум/%s' % ('{:,.0f}'.format(_ei['unit_price']), _ei.get('measure') or 'ед'))
            if _ei.get('photo'):
                parts.append('[📷 Фото](%s)' % _ei['photo'])
            parts.append('Источник: %s' % row['source'])

            # Detail page URL (accessible without auth)
            db_id = _lookup_tender_uuid(row.get('external_id', ''), row['source'])
            if db_id:
                parts.append('%s/%s' % (_DETAIL_PAGE, db_id))
            else:
                url = row.get('source_url', '')
                if url:
                    parts.append(url)

            parts.append('#%s' % kw.replace(' ', '_'))

            # Inline keyboard for feedback
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "\U0001f464 Клиент", "callback_data": "fb:%d:ok" % seq},
                    {"text": "\U0001f4e2 Реклама", "callback_data": "fb:%d:ad" % seq},
                    {"text": "\u274c Мимо", "callback_data": "fb:%d:skip" % seq},
                ]]
            }

            try:
                resp = client.post(bot_url, json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': '\n'.join(parts),
                    'parse_mode': 'Markdown',
                    'disable_web_page_preview': True,
                    'protect_content': True,
                    'reply_markup': reply_markup,
                })
                if resp.status_code == 200:
                    sent += 1
                    resp_data = resp.json()
                    tg_msg_id = None
                    if resp_data.get('ok') and resp_data.get('result'):
                        tg_msg_id = resp_data['result'].get('message_id')
                    _save_alert_seq(row.get('external_id', ''), row['source'], seq, tg_msg_id)
                else:
                    logger.warning('[Alerts] Telegram %d: %s', resp.status_code, resp.text[:200])
            except Exception as exc:
                logger.warning('[Alerts] Error: %s', str(exc))

    logger.info('[Alerts/%s] Sent %d / %d (seq #%d-#%d)', source_label, sent, len(matching), start_seq, start_seq + len(matching) - 1)
    return sent


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

def process_source(rows, source_name, label, dry_run=False, is_plans=False, gate_on_alerted=False):
    # type: (List[Dict[str, Any]], str, str, bool, bool, bool) -> Tuple[int, int, int, int, int]
    """Upsert rows and send alerts. Returns (upserted, new_count, alerts, competitor_alerts, lead_alerts)."""
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
    if alert_rows:
        alerts = send_alerts(alert_rows, label)
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
    parser.add_argument('--source', choices=['plans', 'offers', 'lots', 'auction', 'eshop', 'contracts', 'uzex-auc', 'uzex-prq', 'all'],
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
        u, n, a, c, l = process_source(rows, 'Cooperation.uz Закупочные планы', 'Plans', args.dry_run, is_plans=True)
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

    # 6. UZEX reverse auctions (GEO-BLOCKED from VPS, runs on Mac)
    if args.source in ('all', 'uzex-auc'):
        rows = fetch_and_transform_uzex_auctions()
        u, n, a, c, l = process_source(rows, 'UZEX Обратные аукционы', 'UZEX-Auc', args.dry_run)
        total_upserted += u
        total_new += n
        total_alerts += a
        total_competitor += c
        total_leads += l

    # 7. UZEX prequalifications (GEO-BLOCKED from VPS, runs on Mac)
    if args.source in ('all', 'uzex-prq'):
        rows = fetch_and_transform_uzex_prequest()
        u, n, a, c, l = process_source(rows, 'UZEX Предквалификации', 'UZEX-Prq', args.dry_run)
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
