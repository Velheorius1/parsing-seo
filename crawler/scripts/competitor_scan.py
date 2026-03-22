#!/usr/bin/env python3
"""Competitor Scanner — finds competitor activity across tender platforms.

Sources:
  1. UZEX GetResulted — completed deals with winner names (5000+)
  2. UZEX GetNotResulted — active lots (200+)
  3. Cooperation.uz GetAllPlanSchedule — procurement plans with companyName (375k)

Outputs:
  - Console report with competitor stats
  - JSON file with detailed data
  - Telegram alert (optional)

Run: python3 crawler/scripts/competitor_scan.py [--telegram] [--save]
"""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import httpx

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://oaoehczbycrabkprazts.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_ALERT_CHAT_ID', '')

UZEX_RESULTED_URL = 'https://apietender.uzex.uz/api/CivilContracts/GetResulted'
UZEX_NOT_RESULTED_URL = 'https://apietender.uzex.uz/api/CivilContracts/GetNotResulted'
COOP_PLANS_URL = 'https://new.cooperation.uz/ocelot/api-client/Client/GetAllPlanSchedule'

# Niche keywords for filtering relevant tenders
NICHE_KEYWORDS = [
    'полиграф', 'печат', 'типограф', 'упаков', 'коробк', 'этикет',
    'гофр', 'картон', 'книг', 'каталог', 'брошюр', 'блокнот',
    'календар', 'визитк', 'флаер', 'баннер', 'стенд',
    'bosma', 'qadoq', 'etiket', 'poligraf', 'pechat', 'yorliq', 'quti',
    'bosmaxona', 'chop', 'nashr', 'kitob', 'daftar',
    'ламинац', 'переплёт', 'пакет', 'бланк',
    'blanko', 'jurnal', 'gazeta', 'журнал', 'газет',
]

FALSE_POSITIVES = ['коробка передач', 'противотаранн', 'пластик пакет']


def load_competitors():
    # type: () -> List[str]
    """Load competitor names from Supabase crawler_settings."""
    if not SUPABASE_KEY:
        # Fallback: load from env or hardcoded
        env_val = os.getenv('COMPETITOR_KEYWORDS', '')
        if env_val:
            return [c.strip() for c in env_val.split(',') if c.strip()]
        return []

    try:
        from supabase import create_client
        db = create_client(SUPABASE_URL, SUPABASE_KEY)
        resp = db.table('crawler_settings').select('value').eq('key', 'competitor_keywords').execute()
        if resp.data:
            return json.loads(resp.data[0]['value'])
    except Exception as exc:
        logger.warning('Failed to load competitors from DB: %s', str(exc)[:80])
    return []


def is_niche_tender(title):
    # type: (str) -> bool
    """Check if tender title matches our niche (printing/packaging)."""
    t = title.lower()
    for fp in FALSE_POSITIVES:
        if fp in t:
            return False
    for kw in NICHE_KEYWORDS:
        if kw in t:
            return True
    return False


def match_competitor(name, competitors):
    # type: (str, List[str]) -> Optional[str]
    """Check if a name matches any competitor. Returns matched competitor or None."""
    if not name:
        return None
    name_lower = name.lower().strip()
    for comp in competitors:
        comp_lower = comp.lower()
        if comp_lower in name_lower or name_lower in comp_lower:
            return comp
        # Also match main words (3+ chars)
        comp_words = [w for w in comp_lower.split() if len(w) >= 3]
        if comp_words and all(w in name_lower for w in comp_words):
            return comp
    return None


# ── UZEX Fetcher ────────────────────────────────────────────────

def fetch_uzex_deals(url, limit=5200):
    # type: (str, int) -> List[dict]
    """Fetch deals from UZEX CivilContracts API."""
    all_items = []  # type: List[dict]
    batch = 1000
    for start in range(0, limit, batch):
        try:
            resp = httpx.post(
                url,
                json={'from': start, 'to': start + batch},
                headers={'Content-Type': 'application/json'},
                timeout=30,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data if isinstance(data, list) else []
            all_items.extend(items)
            if len(items) < batch:
                break
        except Exception as exc:
            logger.warning('UZEX fetch error at %d: %s', start, str(exc)[:80])
            break
    return all_items


def scan_uzex_competitors(competitors):
    # type: (List[str]) -> Tuple[List[dict], Dict[str, dict]]
    """Scan UZEX completed + active deals for competitor activity."""
    logger.info('=== UZEX Competitor Scan ===')

    # Fetch completed deals (winners)
    resulted = fetch_uzex_deals(UZEX_RESULTED_URL)
    logger.info('Fetched %d completed deals', len(resulted))

    # Fetch active lots
    not_resulted = fetch_uzex_deals(UZEX_NOT_RESULTED_URL, limit=500)
    logger.info('Fetched %d active lots', len(not_resulted))

    all_deals = resulted + not_resulted
    activities = []  # type: List[dict]
    stats = defaultdict(lambda: {'wins': 0, 'total_value': 0.0, 'max_deal': 0.0,
                                  'categories': set(), 'last_date': ''})

    for item in all_deals:
        provider = (item.get('provider_name') or '').strip()
        matched = match_competitor(provider, competitors)
        if not matched:
            continue

        title = item.get('civil_name', '')
        price = 0.0
        try:
            price = float(item.get('result_cost') or item.get('cost') or 0)
        except (ValueError, TypeError):
            pass

        is_niche = is_niche_tender(title)
        deal_date = item.get('deal_date', '') or item.get('date_ini', '')
        display_id = item.get('display_id', '')
        customer = item.get('customer_name', '')
        status = item.get('status_name', '')

        activity = {
            'competitor': matched,
            'provider_raw': provider,
            'title': title,
            'price': price,
            'customer': customer,
            'date': deal_date[:10] if deal_date else '',
            'display_id': display_id,
            'status': status,
            'is_niche': is_niche,
            'source': 'uzex',
        }
        activities.append(activity)

        # Update stats
        s = stats[matched]
        s['wins'] += 1
        s['total_value'] += price
        if price > s['max_deal']:
            s['max_deal'] = price
        if is_niche and title:
            s['categories'].add(title[:40])
        if deal_date and deal_date > s['last_date']:
            s['last_date'] = deal_date[:10]

    return activities, dict(stats)


# ── Cooperation.uz Fetcher ──────────────────────────────────────

def scan_cooperation_plans(competitors, max_pages=10):
    # type: (List[str], int) -> Tuple[List[dict], Dict[str, dict]]
    """Scan cooperation.uz procurement plans for competitor company names."""
    logger.info('=== Cooperation.uz Plans Scan ===')

    activities = []  # type: List[dict]
    stats = defaultdict(lambda: {'plans': 0, 'total_value': 0.0, 'categories': set(), 'last_date': ''})
    page_size = 500

    for page in range(max_pages):
        try:
            resp = httpx.get(
                COOP_PLANS_URL,
                params={'Skip': page * page_size, 'Take': page_size},
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning('Cooperation API %d at page %d', resp.status_code, page)
                break

            data = resp.json()
            items = data.get('result', {}).get('data', [])
            if not items:
                break

            for item in items:
                # companyName is multilingual dict
                company_raw = item.get('companyName', {})
                if isinstance(company_raw, dict):
                    company = company_raw.get('ru') or company_raw.get('uz') or company_raw.get('cyrl') or ''
                else:
                    company = str(company_raw)

                matched = match_competitor(company, competitors)
                if not matched:
                    continue

                product_raw = item.get('productName', {})
                if isinstance(product_raw, dict):
                    product = product_raw.get('ru') or product_raw.get('uz') or ''
                else:
                    product = str(product_raw)

                plan_num = item.get('planNumber', '')
                created = item.get('createdDate', '')

                activity = {
                    'competitor': matched,
                    'company_raw': company,
                    'title': product,
                    'plan_number': plan_num,
                    'date': created[:10] if created else '',
                    'source': 'cooperation_plans',
                    'is_niche': is_niche_tender(product),
                }
                activities.append(activity)

                s = stats[matched]
                s['plans'] += 1
                if product:
                    s['categories'].add(product[:40])
                if created and created > s['last_date']:
                    s['last_date'] = created[:10]

            logger.info('  Page %d: %d items, found %d matches so far', page, len(items), len(activities))

            if len(items) < page_size:
                break

        except Exception as exc:
            logger.warning('Cooperation fetch error at page %d: %s', page, str(exc)[:80])
            break

    return activities, dict(stats)


# ── Report ──────────────────────────────────────────────────────

def print_report(uzex_stats, coop_stats, uzex_activities):
    # type: (Dict[str, dict], Dict[str, dict], List[dict]) -> str
    """Print competitor report and return as string."""
    lines = []  # type: List[str]
    lines.append('')
    lines.append('=' * 70)
    lines.append('  ОТЧЁТ ПО КОНКУРЕНТАМ — %s' % datetime.now().strftime('%Y-%m-%d %H:%M'))
    lines.append('=' * 70)

    # UZEX wins (sorted by total value)
    lines.append('')
    lines.append('--- UZEX: Победы в тендерах ---')
    lines.append('')

    uzex_sorted = sorted(uzex_stats.items(), key=lambda x: x[1]['total_value'], reverse=True)

    for comp, s in uzex_sorted:
        categories = s.get('categories', set())
        # Convert set to list for serialization
        cat_str = ', '.join(list(categories)[:3]) if categories else '—'
        lines.append('  %-35s %3d побед | %15s UZS | макс: %15s | %s' % (
            comp[:35],
            s['wins'],
            '{:,.0f}'.format(s['total_value']),
            '{:,.0f}'.format(s['max_deal']),
            s.get('last_date', ''),
        ))

    # Niche-specific (our direct competitors)
    niche_activities = [a for a in uzex_activities if a.get('is_niche')]
    if niche_activities:
        lines.append('')
        lines.append('--- НАША НИША (полиграфия/упаковка) ---')
        lines.append('')
        niche_sorted = sorted(niche_activities, key=lambda x: x.get('price', 0), reverse=True)
        for a in niche_sorted[:20]:
            lines.append('  %15s UZS | %-30s | %s | %s' % (
                '{:,.0f}'.format(a.get('price', 0)),
                a['competitor'][:30],
                (a.get('title') or '')[:40],
                a.get('date', ''),
            ))

    # Cooperation plans
    if coop_stats:
        lines.append('')
        lines.append('--- Cooperation.uz: Закупочные планы конкурентов ---')
        lines.append('')
        coop_sorted = sorted(coop_stats.items(), key=lambda x: x[1]['plans'], reverse=True)
        for comp, s in coop_sorted[:15]:
            categories = s.get('categories', set())
            cat_str = ', '.join(list(categories)[:3]) if categories else '—'
            lines.append('  %-35s %3d планов | %s | %s' % (
                comp[:35], s['plans'], cat_str[:50], s.get('last_date', '')))

    lines.append('')
    lines.append('=' * 70)

    report = '\n'.join(lines)
    print(report)
    return report


def send_telegram_alert(report_text):
    # type: (str) -> None
    """Send competitor report summary to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning('Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_ALERT_CHAT_ID)')
        return

    # Truncate for Telegram (4096 char limit)
    text = report_text[:3900]
    try:
        resp = httpx.post(
            'https://api.telegram.org/bot%s/sendMessage' % TELEGRAM_BOT_TOKEN,
            json={
                'chat_id': TELEGRAM_CHAT_ID,
                'text': text,
                'disable_web_page_preview': True,
                'disable_notification': True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info('Telegram alert sent')
        else:
            logger.warning('Telegram send failed: %d', resp.status_code)
    except Exception as exc:
        logger.warning('Telegram error: %s', str(exc)[:80])


def save_json(uzex_activities, coop_activities, uzex_stats, coop_stats):
    # type: (List[dict], List[dict], Dict[str, dict], Dict[str, dict]) -> str
    """Save detailed results to JSON file."""
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output')
    os.makedirs(output_dir, exist_ok=True)

    filename = 'competitor_report_%s.json' % datetime.now().strftime('%Y%m%d_%H%M')
    filepath = os.path.join(output_dir, filename)

    # Convert sets to lists for JSON serialization
    for stats_dict in [uzex_stats, coop_stats]:
        for comp, s in stats_dict.items():
            if 'categories' in s and isinstance(s['categories'], set):
                s['categories'] = list(s['categories'])

    data = {
        'generated_at': datetime.now().isoformat(),
        'uzex_stats': uzex_stats,
        'coop_stats': coop_stats,
        'uzex_activities': uzex_activities,
        'coop_activities': coop_activities,
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info('Saved to %s', filepath)
    return filepath


# ── Main ────────────────────────────────────────────────────────

def main():
    # type: () -> None
    do_telegram = '--telegram' in sys.argv
    do_save = '--save' in sys.argv
    skip_coop = '--no-coop' in sys.argv

    # Load competitors
    competitors = load_competitors()
    if not competitors:
        logger.error('No competitors loaded. Set SUPABASE_SERVICE_ROLE_KEY or COMPETITOR_KEYWORDS env.')
        return
    logger.info('Loaded %d competitors', len(competitors))

    # Scan UZEX
    uzex_activities, uzex_stats = scan_uzex_competitors(competitors)
    logger.info('UZEX: %d activities, %d unique competitors found', len(uzex_activities), len(uzex_stats))

    # Scan Cooperation.uz (may fail if geo-blocked)
    coop_activities = []  # type: List[dict]
    coop_stats = {}  # type: Dict[str, dict]
    if not skip_coop:
        try:
            coop_activities, coop_stats = scan_cooperation_plans(competitors)
            logger.info('Cooperation: %d activities, %d unique competitors', len(coop_activities), len(coop_stats))
        except Exception as exc:
            logger.warning('Cooperation scan failed (geo-blocked?): %s', str(exc)[:80])

    # Report
    report = print_report(uzex_stats, coop_stats, uzex_activities)

    # Save
    if do_save:
        save_json(uzex_activities, coop_activities, uzex_stats, coop_stats)

    # Telegram
    if do_telegram:
        send_telegram_alert(report)


if __name__ == '__main__':
    main()
