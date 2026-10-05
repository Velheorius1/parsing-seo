#!/usr/bin/env python3
"""Fetch ebirja contracts (winners, prices) and upsert to Supabase.

List: the public API behind xarid.ebirja.uz/ru/contracts/{type}
(collect_ebirja_contract_api), walked down to a date boundary. Until 05.10.2026
the list was the Playwright page and its pager never moved past page 1: 20
contracts per type per run, the e-shop landed at ~19% (209 of 1080 for
14–20.09). Rows from the API are built in exactly the card format — checked
field by field on 310 contracts already in the DB.

--detail still opens the public card in Playwright (start price, discount),
only for contracts the DB has not enriched yet.

Usage:
    python3 -m crawler.scripts.fetch_ebirja_contracts                 # all types, last 3 days
    python3 -m crawler.scripts.fetch_ebirja_contracts --type shop     # e-shop only
    python3 -m crawler.scripts.fetch_ebirja_contracts --dry-run       # no DB writes
    python3 -m crawler.scripts.fetch_ebirja_contracts --since 2026-01-01   # history
    python3 -m crawler.scripts.fetch_ebirja_contracts --detail        # + card pages (slow)

Requires: httpx, supabase, python-dotenv; playwright only for --detail
"""

import argparse
import asyncio
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

def _send_telegram_alert(text):
    # type: (str) -> None
    """Send alert to Telegram via Bot API. Silently fails if tokens missing."""
    try:
        from crawler.config.settings import settings
        bot_token = settings.telegram_bot_token or ''
        chat_id = settings.telegram_alert_chat_id or ''
    except Exception:
        import os
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.getenv('TELEGRAM_ALERT_CHAT_ID', '')

    if not bot_token or not chat_id:
        logger.warning('Telegram alert skipped: bot_token or chat_id not set')
        return
    try:
        import httpx
        url = 'https://api.telegram.org/bot%s/sendMessage' % bot_token
        r = httpx.post(url, json={
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }, timeout=10)
        if r.status_code != 200:
            logger.warning('Telegram alert failed: %s', r.text[:100])
    except Exception as exc:
        logger.warning('Telegram alert error: %s', str(exc)[:80])


CONTRACT_TYPES = {
    'shop': {'url': 'https://xarid.ebirja.uz/ru/contracts/shop', 'name': 'Ebirja Договоры (Э-магазин)'},
    'auction': {'url': 'https://xarid.ebirja.uz/ru/contracts/auction', 'name': 'Ebirja Договоры (Аукцион)'},
    'tender': {'url': 'https://xarid.ebirja.uz/ru/contracts/tender', 'name': 'Ebirja Договоры (Тендер)'},
    'selection': {'url': 'https://xarid.ebirja.uz/ru/contracts/selection', 'name': 'Ebirja Договоры (Отбор)'},
}


# Код валюты в API → подпись карточки. Сумы приходят как "000".
_API_CURRENCY = {'000': 'UZS', '840': 'USD', '978': 'EUR', '643': 'RUB'}
CARD_URL = 'https://ebirja.uz/ru/contracts/%s/%s'
API_PAGE_SIZE = 100
API_PAGE_CAP = 500  # 50 000 договоров на тип: граница по дате наступает раньше
HEARTBEAT_ROWS = 20  # столько же, сколько давала первая страница сайта


def _ws(value):
    # type: (Any,) -> str
    """Пробелы схлопнуты, как в innerText карточки."""
    return ' '.join(str(value or '').split())


def api_row(contract_type, item):
    # type: (str, Dict[str, Any]) -> Optional[Dict[str, Any]]
    """Строка tenders из записи API (normalize) — та же, что давал разбор карточки.

    None — валюта, которой нет в _API_CURRENCY, или запись без номера и даты.
    """
    currency = _API_CURRENCY.get(str((item.get('raw') or {}).get('currency') or ''))
    number, lot = _ws(item.get('contract_number')), _ws(item.get('lot_number'))
    if not currency or not (number or lot):
        return None
    try:
        created = datetime.strptime(str(item.get('awarded_at')), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    buyer, executor = _ws(item.get('buyer_name')), _ws(item.get('winner_name'))
    amount = item.get('amount')
    return {
        'external_id': 'ebirja-ctr-%s' % (number or lot),
        'title': 'Договор %s | %s' % (number, lot) if number else lot,
        'organization': buyer,
        'winner_name': executor,
        'price': float(amount) if amount is not None else None,
        'currency': currency,
        'deadline': created.strftime('%d.%m.%Y'),
        'source': CONTRACT_TYPES[contract_type]['name'],
        'source_url': CARD_URL % (contract_type, item.get('procedure_id')),
        'status': 'completed',
        'search_text': ' '.join(filter(None, [buyer, executor, lot, number])),
        'region': '',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'message_type': 'contract',
    }


def fetch_contracts_api(contract_type, date_from, collect=None):
    # type: (str, date, Optional[Callable[..., Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]
    """Договоры типа с date_from по API плюс HEARTBEAT_ROWS самых свежих.

    Свежие — пульс источника: у тендера бывает 3 договора за два
    месяца, окно в несколько дней пустое, collected_at стареет, и сторож
    (36 ч, healthcheck sources.scripted) кричит «молчит» на живом источнике.
    Браузер тоже каждый прогон перезаписывал первую страницу сайта (20).
    """
    from crawler.scripts.collect_ebirja_contract_api import collect_source, normalize
    res = (collect or collect_source)(contract_type, date_from, API_PAGE_SIZE, API_PAGE_CAP)
    items = list(res['rows'])
    if res['raw_pages']:
        items.extend(normalize(contract_type, r)
                     for r in res['raw_pages'][0]['rows'][:HEARTBEAT_ROWS])
    rows = []  # type: List[Dict[str, Any]]
    ids = set()
    skipped = set()
    for item in items:
        row = api_row(contract_type, item)
        if row is None:
            skipped.add(item.get('procedure_id'))
        elif row['external_id'] not in ids:
            # Окно и первая страница пересекаются, а один external_id дважды
            # в пачке upsert роняет её целиком («cannot affect row a second time»).
            ids.add(row['external_id'])
            rows.append(row)
    return rows, {
        'complete': res['complete'],
        'completion': res['completion'],
        'pages': res['pages_collected'],
        'skipped': len(skipped),
    }


def _parse_price(text):
    # type: (str) -> Optional[float]
    """Extract numeric price from text like '567 000 000 UZS'."""
    if not text:
        return None
    cleaned = re.sub(r'[^\d.,]', '', text.replace(' ', ''))
    try:
        return float(cleaned.replace(',', '.'))
    except (ValueError, TypeError):
        return None


async def _extract_cards_from_page(page):
    # type: (Any,) -> List[Dict[str, str]]
    """Extract contract card data from the current page DOM."""
    cards_data = await page.evaluate('''() => {
        const containers = document.querySelectorAll('div.rounded-\\\\[16px\\\\]');
        if (containers.length === 0) {
            // Fallback: find div with border
            const fallback = document.querySelectorAll('div[class*="border-2"][class*="rounded"]');
            if (fallback.length > 0) {
                return Array.from(fallback).map(c => ({text: c.innerText, link: (c.querySelector('a') || {}).href || ''}));
            }
            return [];
        }
        return Array.from(containers).map(c => ({
            text: c.innerText,
            link: (c.querySelector('a') || {}).href || ''
        }));
    }''')

    if not cards_data:
        # Try alternative container
        cards_data = await page.evaluate('''() => {
            const cards = document.querySelectorAll('div.mb-3');
            return Array.from(cards).map(c => {
                const p = c.parentElement;
                return {
                    text: p ? p.innerText : c.innerText,
                    link: (p ? p.querySelector('a') : c.querySelector('a') || {}).href || ''
                };
            });
        }''')

    return cards_data or []


async def _click_next_page(page):
    # type: (Any,) -> bool
    """Try to click the next-page button. Return True if navigated, False if no more pages."""
    has_next = await page.evaluate('''() => {
        // Look for pagination buttons: "›", "»", or button with aria-label "next"
        const allBtns = document.querySelectorAll('button, a');
        for (const btn of allBtns) {
            const txt = btn.textContent.trim();
            if (txt === '›' || txt === '»' || txt === 'Next' || txt === 'Keyingi') {
                // Check if disabled
                if (btn.disabled || btn.classList.contains('disabled') || btn.getAttribute('aria-disabled') === 'true') {
                    return false;
                }
                btn.click();
                return true;
            }
        }
        // Also try numbered pagination: find active page, click next number
        const active = document.querySelector('button.bg-primary, button[aria-current="page"], li.active button, li.active a');
        if (active) {
            const currentNum = parseInt(active.textContent.trim(), 10);
            if (!isNaN(currentNum)) {
                const allPagBtns = document.querySelectorAll('button, a');
                for (const btn of allPagBtns) {
                    if (btn.textContent.trim() === String(currentNum + 1)) {
                        btn.click();
                        return true;
                    }
                }
            }
        }
        return false;
    }''')
    if has_next:
        await page.wait_for_timeout(3000)
    return has_next


async def fetch_contracts_page(page, url, contract_type, max_pages=1):
    # type: (Any, str, str, int) -> List[Dict[str, Any]]
    """Fetch contracts with pagination support."""
    await page.goto(url, wait_until='domcontentloaded', timeout=45000)
    await page.wait_for_timeout(5000)

    all_contracts = []  # type: List[Dict[str, Any]]

    for page_num in range(1, max_pages + 1):
        logger.info('  Page %d/%d ...', page_num, max_pages)
        cards_data = await _extract_cards_from_page(page)

        page_contracts = []
        for card in cards_data:
            text = card.get('text', '')
            link = card.get('link', '')
            if not text or len(text) < 20:
                continue
            parsed = _parse_contract_text(text, link, contract_type)
            if parsed:
                page_contracts.append(parsed)

        logger.info('  Page %d: %d contracts', page_num, len(page_contracts))
        all_contracts.extend(page_contracts)

        if not page_contracts:
            logger.info('  No contracts on page %d, stopping pagination', page_num)
            break

        # Navigate to next page if not the last requested page
        if page_num < max_pages:
            navigated = await _click_next_page(page)
            if not navigated:
                logger.info('  No next page button found, stopping at page %d', page_num)
                break

    return all_contracts


async def fetch_contract_detail(page, source_url):
    # type: (Any, str) -> Dict[str, Any]
    """Fetch detail page for a single contract.

    Returns dict with: winner_name, start_price, offer_price, discount_pct.
    """
    detail = {
        'winner_name': '',
        'start_price': None,
        'offer_price': None,
        'discount_pct': None,
    }  # type: Dict[str, Any]

    if not source_url or not source_url.startswith('http'):
        return detail

    try:
        await page.goto(source_url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(3000)

        raw = await page.evaluate('''() => {
            const result = {winner: '', startPrice: '', offerPrice: ''};
            const body = document.body.innerText;

            // Split by lines and find exact matches
            const lines = body.split('\\n').map(l => l.trim());
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];

                // Winner: exact label match
                if (line === 'Наименование исполнителя:' || line === 'Bajaruvchi nomi:' ||
                    line === 'Наименование исполнителя' || line === 'Bajaruvchi nomi') {
                    if (i + 1 < lines.length) result.winner = lines[i + 1];
                }

                // Start price: MUST be "Общая начальная цена" (not "за единицу")
                if ((line === 'Общая начальная цена' || line === 'Umumiy boshlang\\'ich narx' ||
                     line.startsWith('Общая начальная цена')) &&
                    !line.includes('за единицу') && !line.includes('birlik')) {
                    if (i + 1 < lines.length && lines[i + 1].includes('UZS')) {
                        result.startPrice = lines[i + 1];
                    }
                }

                // Offer price: MUST be "Общая цена предложения" (not "за единицу")
                if ((line === 'Общая цена предложения' || line === 'Umumiy taklif narxi' ||
                     line.startsWith('Общая цена предложения')) &&
                    !line.includes('за единицу') && !line.includes('birlik')) {
                    if (i + 1 < lines.length && lines[i + 1].includes('UZS')) {
                        result.offerPrice = lines[i + 1];
                    }
                }
            }

            return result;
        }''')

        detail['winner_name'] = raw.get('winner', '').strip()
        detail['start_price'] = _parse_price(raw.get('startPrice', ''))
        detail['offer_price'] = _parse_price(raw.get('offerPrice', ''))

        # Calculate discount
        if detail['start_price'] and detail['offer_price'] and detail['start_price'] > 0:
            detail['discount_pct'] = round(
                (detail['start_price'] - detail['offer_price']) / detail['start_price'] * 100, 2
            )

    except Exception as exc:
        logger.warning('Detail page error for %s: %s', source_url, str(exc)[:100])

    return detail


def _parse_contract_text(text, link, contract_type):
    # type: (str, str, str) -> Optional[Dict[str, Any]]
    """Parse contract card text into structured data."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    lot_number = ''
    contract_number = ''
    date = ''
    buyer = ''
    executor = ''
    price_text = ''
    status = ''

    for i, line in enumerate(lines):
        if line.startswith('№') or line.startswith('#'):
            lot_number = line.lstrip('№#').strip()
        elif re.match(r'\d{2}\.\d{2}\.\d{4}', line):
            date = line
        elif 'Договор №' in line or 'Shartnoma' in line:
            contract_number = line.replace('Договор №', '').replace('Shartnoma №', '').strip()
        elif line == 'Заказчик:' or line == 'Buyurtmachi:':
            if i + 1 < len(lines):
                buyer = lines[i + 1]
        elif line == 'Исполнитель:' or line == 'Bajaruvchi:':
            if i + 1 < len(lines):
                executor = lines[i + 1]
        elif 'Цена победителя:' in line or 'G\'olib narxi:' in line:
            if i + 1 < len(lines):
                price_text = lines[i + 1]
            else:
                price_text = line.split(':')[-1].strip()
        elif 'UZS' in line and not price_text:
            price_text = line
        elif line == 'Статус:' or line == 'Holat:':
            if i + 1 < len(lines):
                status = lines[i + 1]

    if not lot_number and not contract_number:
        return None

    ext_id = contract_number or lot_number
    price = _parse_price(price_text)

    # Build source_url from link
    source_url = link if link.startswith('http') else ''

    return {
        'external_id': 'ebirja-ctr-%s' % ext_id,
        'title': 'Договор %s | %s' % (contract_number, lot_number) if contract_number else lot_number,
        'organization': buyer,
        # Kept separately for read-only audit collectors.  The production path
        # still has search_text for backwards compatibility, but an audit must
        # never try to recover an entity name by parsing that free-text field.
        'winner_name': executor,
        'price': price,
        'currency': 'UZS',
        'deadline': date,
        'source': CONTRACT_TYPES.get(contract_type, {}).get('name', contract_type),
        'source_url': source_url,
        'status': 'completed',
        'search_text': ' '.join(filter(None, [buyer, executor, lot_number, contract_number])),
        'region': '',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'message_type': 'contract',
    }


# Поля разбора, которых нет в таблице tenders: их читает только аудиторский
# сборщик (collect_ebirja_contracts). 21.09 (5ef854d) winner_name ушёл в боевой
# upsert — PGRST204 на каждом прогоне, «Upserted: 0» до 05.10, и ни одной тревоги.
_AUDIT_ONLY_FIELDS = ('winner_name',)


def db_row(row):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Строка ровно в колонки tenders — без полей только для аудита."""
    return {k: v for k, v in row.items() if k not in _AUDIT_ONLY_FIELDS}


def _client():
    # type: () -> Any
    from crawler.config.settings import settings
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def enriched_search_text(client, ext_ids, chunk=50):
    # type: (Any, List[str], int) -> Dict[str, str]
    """search_text с данными --detail (winner:/discount:) по external_id.

    Пачка 50: длинный in_ на self-hosted Supabase отдаёт 502 от nginx (05.10 —
    уже на 200 id). Сбой после трёх попыток — исключение, а не пустой словарь:
    иначе upsert молча затрёт обогащённые строки свежими из списка.
    """
    enriched = {}  # type: Dict[str, str]
    for start in range(0, len(ext_ids), chunk):
        part = ext_ids[start:start + chunk]
        for attempt in range(1, 4):
            try:
                res = client.table('tenders').select(
                    'external_id, search_text'
                ).in_('external_id', part).execute()
                break
            except Exception as exc:
                if attempt == 3:
                    raise
                logger.warning('Read search_text attempt %d/3 failed: %s', attempt, str(exc)[:80])
                time.sleep(2 ** (attempt - 1))
        for row in (res.data or []):
            st = row.get('search_text') or ''
            if 'winner:' in st or 'discount:' in st:
                enriched[row['external_id']] = st
    return enriched


def upsert_to_supabase(rows, dry_run=False, enriched=None):
    # type: (List[Dict[str, Any]], bool, Optional[Dict[str, str]]) -> int
    """Upsert rows to Supabase tenders table."""
    if not rows:
        return 0
    if dry_run:
        logger.info('[DRY-RUN] Would upsert %d contracts', len(rows))
        for r in rows[:5]:
            search = r.get('search_text', '')
            extra = ''
            if 'winner:' in search:
                extra = ' | ' + search.split('| ', 1)[-1][:60] if '|' in search else ''
            logger.info('  %s | %s | %s %s%s',
                        r['external_id'], r['organization'][:30],
                        r.get('price', '?'), r.get('currency', ''), extra)
        if len(rows) > 5:
            logger.info('  ... and %d more', len(rows) - 5)
        return len(rows)

    client = _client()

    # Phase 4: Preserve enriched search_text (winner/discount data from --detail runs)
    if enriched is None:
        enriched = enriched_search_text(client, [r['external_id'] for r in rows])
    preserved = 0
    for row in rows:
        old = enriched.get(row['external_id'])
        new_st = row.get('search_text', '')
        if old and 'winner:' not in new_st and 'discount:' not in new_st:
            row['search_text'] = old
            preserved += 1
    if preserved:
        logger.info('Preserved enriched search_text for %d contracts', preserved)

    batch_size = 500
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = [db_row(r) for r in rows[i:i + batch_size]]
        # Retry upsert up to 3 times with exponential backoff
        for attempt in range(1, 4):
            try:
                client.table('tenders').upsert(
                    batch, on_conflict='external_id,source'
                ).execute()
                total += len(batch)
                logger.info('Upserted batch %d (%d rows)', i // batch_size + 1, len(batch))
                break
            except Exception as exc:
                if attempt < 3:
                    delay = 2 ** (attempt - 1)
                    logger.warning('Upsert attempt %d/3 failed: %s. Retrying in %ds...',
                                   attempt, str(exc)[:80], delay)
                    time.sleep(delay)
                else:
                    logger.error('Upsert failed after 3 attempts: %s', str(exc)[:120])
    return total


async def _enrich_with_details(browser, contracts, dry_run=False):
    # type: (Any, List[Dict[str, Any]], bool) -> List[Dict[str, Any]]
    """Fetch detail pages for each contract and enrich data."""
    if not contracts:
        return contracts

    logger.info('Fetching detail pages for %d contracts (rate: 1 req / 2s)...', len(contracts))
    detail_page = await browser.new_page()

    try:
        for idx, contract in enumerate(contracts):
            url = contract.get('source_url', '')
            if not url:
                continue

            logger.info('  Detail %d/%d: %s', idx + 1, len(contracts), url)

            if dry_run:
                logger.info('    [DRY-RUN] Would fetch detail page')
                continue

            detail = await fetch_contract_detail(detail_page, url)

            # Enrich contract data
            if detail['winner_name']:
                # Prepend winner to search_text for searchability
                parts = contract.get('search_text', '').split()
                if detail['winner_name'] not in contract.get('search_text', ''):
                    contract['search_text'] = ' '.join(
                        filter(None, [contract.get('organization', ''),
                                      detail['winner_name']] + parts[1:])
                    )

            if detail['offer_price'] is not None:
                # Use offer_price as the main price (winner price)
                contract['price'] = detail['offer_price']

            # Encode detail data in search_text (no extra DB columns needed)
            # Format: "buyer | winner | lot | contract | start:N | discount:N%"
            extra_parts = []
            if detail['winner_name']:
                extra_parts.append('winner:%s' % detail['winner_name'])
            if detail['start_price'] is not None:
                extra_parts.append('start_price:%.0f' % detail['start_price'])
            if detail['discount_pct'] is not None:
                extra_parts.append('discount:%.1f%%' % detail['discount_pct'])
            if extra_parts:
                contract['search_text'] = contract.get('search_text', '') + ' | ' + ' | '.join(extra_parts)

            # Rate limit: 2 seconds between detail page requests
            await asyncio.sleep(2)
    finally:
        await detail_page.close()

    return contracts


def _date_from(args):
    # type: (Any,) -> date
    if args.since:
        return date.fromisoformat(args.since)
    return date.today() - timedelta(days=args.days)


async def _detail_pass(rows, per_type, dry_run):
    # type: (List[Dict[str, Any]], int, bool) -> None
    """Карточки в Playwright — только для ещё не обогащённых, по per_type на тип.

    Список по API даёт ~150 договоров магазина в день; карточка — ~5 с, поэтому
    без лимита вечерний прогон растянулся бы на десятки минут.
    """
    enriched = {} if dry_run else enriched_search_text(_client(), [r['external_id'] for r in rows])
    pending = []  # type: List[Dict[str, Any]]
    taken = {}  # type: Dict[str, int]
    for row in rows:
        if row['external_id'] in enriched or taken.get(row['source'], 0) >= per_type:
            continue
        taken[row['source']] = taken.get(row['source'], 0) + 1
        pending.append(row)
    if not pending:
        return
    if dry_run:
        logger.info('[DRY-RUN] Would open %d card pages', len(pending))
        return
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            await _enrich_with_details(browser, pending, dry_run=dry_run)
        finally:
            await browser.close()


async def main_async(args):
    types_to_fetch = [args.type] if args.type != 'all' else list(CONTRACT_TYPES.keys())
    date_from = _date_from(args)
    all_rows = []  # type: List[Dict[str, Any]]
    incomplete = []  # type: List[str]

    for ctype in types_to_fetch:
        info = CONTRACT_TYPES[ctype]
        try:
            contracts, run = fetch_contracts_api(ctype, date_from)
        except Exception as exc:
            logger.warning('Failed to fetch %s: %s', ctype, str(exc)[:100])
            _send_telegram_alert(
                '<b>Ebirja Contracts ALERT</b>\nFailed to fetch %s: %s' % (ctype, str(exc)[:100])
            )
            incomplete.append('%s (ошибка)' % ctype)
            continue
        logger.info('%s: %d contracts since %s (pages %d, %s, skipped %d)',
                    info['name'], len(contracts), date_from, run['pages'],
                    run['completion'], run['skipped'])
        if not run['complete']:
            incomplete.append('%s (%s)' % (ctype, run['completion']))
        all_rows.extend(contracts)

    logger.info('Total contracts: %d', len(all_rows))
    if args.detail and all_rows:
        try:
            await _detail_pass(all_rows, args.detail_limit, args.dry_run)
        except Exception as exc:
            # Карточки — довесок: без них список всё равно пишем.
            logger.warning('Detail pass failed: %s', str(exc)[:120])

    try:
        upserted = upsert_to_supabase(all_rows, dry_run=args.dry_run)
    except Exception as exc:
        logger.error('Upsert aborted: %s', str(exc)[:160])
        _send_telegram_alert(
            '<b>Ebirja Contracts ALERT</b>\nНе записал %d договоров: %s' % (len(all_rows), str(exc)[:100])
        )
        sys.exit(1)
    logger.info('Done! Fetched: %d, Upserted: %d', len(all_rows), upserted)

    if len(all_rows) == 0:
        _send_telegram_alert(
            '<b>Ebirja Contracts ALERT</b>\n0 contracts fetched. Types: %s' % ', '.join(types_to_fetch)
        )
        sys.exit(1)
    # Скачали, но не записали — тоже поломка, а не «тихий» прогон: 21.09–05.10
    # каждый запуск писал «Fetched: 63, Upserted: 0» и тревоги не было.
    if upserted < len(all_rows):
        _send_telegram_alert(
            '<b>Ebirja Contracts ALERT</b>\nЗаписано в базу %d из %d договоров — см. /var/log/parsing-seo.log'
            % (upserted, len(all_rows))
        )
        sys.exit(1)
    # Лист не дошёл до границы по дате — записано не всё окно.
    if incomplete:
        _send_telegram_alert(
            '<b>Ebirja Contracts ALERT</b>\nСписок неполный с %s: %s' % (date_from, ', '.join(incomplete))
        )
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Fetch ebirja contracts')
    parser.add_argument('--type', choices=list(CONTRACT_TYPES.keys()) + ['all'], default='all')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--days', type=int, default=3,
                        help='Window: contracts of the last N days (default: 3; runs are twice a day)')
    parser.add_argument('--since', default=None, help='Window start YYYY-MM-DD, overrides --days')
    parser.add_argument('--detail', action='store_true',
                        help='Open card pages for contracts not enriched yet (slow, ~5s per contract)')
    parser.add_argument('--detail-limit', type=int, default=20,
                        help='Card pages per type for --detail (default: 20)')
    args = parser.parse_args()
    if args.days < 1 or args.detail_limit < 0:
        parser.error('--days must be positive, --detail-limit non-negative')
    try:
        _date_from(args)
    except ValueError:
        parser.error('--since must be YYYY-MM-DD')

    asyncio.run(main_async(args))


if __name__ == '__main__':
    main()
