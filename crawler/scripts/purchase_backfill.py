#!/usr/bin/env python3
"""Сбор журнала закупок (purchase_ledger) из публичных лент.

Списки — от новых к старым до границы --since:
  deals     сделки etender (DealsList)           ИНН заказчика и предмет в строке
  direct    прямые закупки UZEX                  ИНН в строке, предмет — в деталях
  civil     итоги ВМК-69 (CivilContracts)        ИНН и предмет в строке
  ebirja    договоры ebirja, 4 типа              ИНН и предмет — только в карточке
Каждая страница — gzip-квитанция в data/purchase-raw/<feed>/ (можно
переклассифицировать без повторного сбора).

Детали (--details N): карточки ebirja и позиции прямых закупок в «печатных»
разделах, новые первыми; остальные прямые закупки (~97%) деталей не требуют.

  python3 -m crawler.scripts.purchase_backfill --feed all --since 2024-10-07   # история, продолжает с места
  python3 -m crawler.scripts.purchase_backfill --feed all --incremental        # ежедневно: последние 7 дней
  python3 -m crawler.scripts.purchase_backfill --details 2500 --max-minutes 90
  ... --dry-run                                                                # без записи в БД
"""
import argparse
import gzip
import hashlib
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from crawler.core import purchase_ledger as L
from crawler.core.competitor_audit import page_body

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

RAW_DIR = Path(__file__).resolve().parents[2] / 'data' / 'purchase-raw'
TABLE = 'purchase_ledger'
UZEX_ENDPOINTS = {
    'deals': 'https://apietender.uzex.uz/api/common/DealsList',
    'direct': 'https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases',
    'civil': 'https://apietender.uzex.uz/api/CivilContracts/GetResulted',
}
DIRECT_DETAIL_URL = 'https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchase/%s'
EBIRJA_CARD_ENDPOINTS = {
    'shop': 'https://xarid-api.ebirja.uz/common/contract/shop-view',
    'auction': 'https://xarid-api.ebirja.uz/common/contract/external-auction-view',
    'tender': 'https://xarid-api.ebirja.uz/common/contract/external-tender-view',
    'selection': 'https://xarid-api.ebirja.uz/common/contract/external-tender-view',
}
# Разделы прямых закупок, где читаем детали — то же, что L.needs_details, но
# фильтром PostgREST (`*` вместо пробела: пробел и запятая ломают or=()).
_DIRECT_DETAIL_ILIKE = ('*издат*', '*печат*', '*бумаг*', '*одежд*', '*текстил*', '*реклам*',
                        '*изделия*готовые*прочие*', '*минеральные*неметаллические*', '*резин*',
                        '*пластмасс*', '*кож*')
PAGE_SIZE = 500
INCREMENTAL_DAYS = 7


def scrub(value):
    # type: (Any) -> Any
    """Убирает NUL из строк ответа API: Postgres не хранит \u0000 в text
    (22P05), и одна такая запись сделки 06.2025 роняла всю догрузку истории."""
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def _client():
    # type: () -> Any
    from crawler.core.db import _get_client
    return _get_client()


def _receipt(feed, page, payload):
    # type: (str, int, Any) -> str
    """gzip-квитанция страницы; возвращает sha256 сырого ответа."""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    digest = hashlib.sha256(body).hexdigest()
    now = datetime.now(timezone.utc)
    folder = RAW_DIR / feed / now.strftime('%Y-%m-%d')
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(folder / ('%s-p%05d.json.gz' % (now.strftime('%H%M%S'), page))), 'wb') as fh:
        fh.write(body)
    return digest


def _state_path(feed):
    # type: (str) -> Path
    return RAW_DIR / feed / 'state.json'


def _load_state(feed, since):
    # type: (str, str) -> Dict[str, Any]
    try:
        state = json.loads(_state_path(feed).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'since': since, 'next_page': 0, 'complete': False}
    if state.get('since') != since:
        return {'since': since, 'next_page': 0, 'complete': False}
    return state


def _save_state(feed, state):
    # type: (str, Dict[str, Any]) -> None
    path = _state_path(feed)
    path.parent.mkdir(parents=True, exist_ok=True)
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')


def upsert_rows(client, payloads, dry_run):
    # type: (Any, List[Dict[str, Any]], bool) -> int
    """upsert пачками по 500 по ключу (feed, business_id); дубли ключа внутри пачки
    роняют её целиком в Postgres — оставляем последнюю версию строки."""
    unique = {}  # type: Dict[Tuple[str, str], Dict[str, Any]]
    for payload in payloads:
        unique[(payload['feed'], payload['business_id'])] = payload
    rows = list(unique.values())
    if dry_run or not rows:
        return len(rows)
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for start in range(0, len(rows), 500):
        batch = [dict(r, updated_at=now) for r in rows[start:start + 500]]
        for attempt in range(1, 4):
            try:
                client.table(TABLE).upsert(batch, on_conflict='feed,business_id').execute()
                written += len(batch)
                break
            except Exception as exc:
                if attempt == 3:
                    raise
                logger.warning('upsert attempt %d/3 failed: %s', attempt, str(exc)[:120])
                time.sleep(2 ** attempt)
    return written


def _row_day(feed, raw):
    # type: (str, Dict[str, Any]) -> Optional[str]
    return L.iso_day(raw.get('contract_date') if feed == 'direct' else raw.get('deal_date'))


def collect_uzex(feed, since, client, dry_run, deadline, pause, incremental,
                 post=None):
    # type: (str, date, Any, bool, float, float, bool, Optional[Callable[..., Any]]) -> Dict[str, Any]
    """Лента UZEX от новых к старым до since. История продолжается с сохранённой
    страницы: новые строки только сдвигают старые вниз, поэтому продолжение с
    той же страницы перечитывает несколько строк, но не пропускает ни одной."""
    post = post or httpx.post
    since_text = since.isoformat()
    state = {'since': since_text, 'next_page': 0, 'complete': False} if incremental \
        else _load_state(feed, since_text)
    if state.get('complete') and not incremental:
        return {'feed': feed, 'completion': 'already_complete', 'complete': True, 'rows': 0, 'pages': 0}
    page = int(state.get('next_page') or 0)
    pages = written = 0
    completion = 'time_budget'
    while time.time() < deadline:
        body = page_body(feed, page, PAGE_SIZE)
        try:
            response = post(UZEX_ENDPOINTS[feed], json=body, timeout=60,
                            headers={'Content-Type': 'application/json'})
            response.raise_for_status()
            raw_rows = response.json()
        except Exception as exc:
            completion = 'error: %s' % str(exc)[:120]
            break
        if not isinstance(raw_rows, list):
            completion = 'schema_error'
            break
        if not raw_rows:
            completion = 'archive_end'
            break
        raw_rows = scrub(raw_rows)
        digest = _receipt(feed, page, raw_rows)
        days = [d for d in (_row_day(feed, r) for r in raw_rows) if d]
        rows = [L.from_uzex(feed, r, digest) for r in raw_rows]
        payloads = [L.list_payload(r) for r in rows
                    if r is not None and r['awarded_on'] and r['awarded_on'] >= since_text]
        try:
            written += upsert_rows(client, payloads, dry_run)
        except Exception as exc:
            # Страница не записана — состояние не двигаем, следующий прогон её повторит.
            completion = 'error: upsert %s' % str(exc)[:110]
            break
        pages += 1
        page += 1
        if not incremental:
            state['next_page'] = page
            _save_state(feed, state)
        if days and max(days) < since_text:
            completion = 'date_boundary'
            break
        time.sleep(pause)
    complete = completion in ('archive_end', 'date_boundary')
    if complete and not incremental:
        state['complete'] = True
        _save_state(feed, state)
    return {'feed': feed, 'completion': completion, 'complete': complete, 'rows': written, 'pages': pages,
            'next_page': page}


def collect_ebirja(since, client, dry_run, collect=None):
    # type: (date, Any, bool, Optional[Callable[..., Dict[str, Any]]]) -> List[Dict[str, Any]]
    """Списки ebirja (4 типа) — тем же API, что fetch_ebirja_contracts."""
    from crawler.scripts.collect_ebirja_contract_api import collect_source, normalize
    collect = collect or collect_source
    since_text = since.isoformat()
    results = []
    for source_key in L.EBIRJA_FEEDS:
        res = collect(source_key, since, 100, 2000)
        payloads = []
        for index, page in enumerate(res.get('raw_pages') or []):
            digest = _receipt(L.EBIRJA_FEEDS[source_key], index, page.get('rows') or [])
            for raw in scrub(page.get('rows') or []):
                row = L.from_ebirja_list(source_key, normalize(source_key, raw), digest)
                if row is not None and row['awarded_on'] and row['awarded_on'] >= since_text:
                    payloads.append(L.list_payload(row))
        completion = res.get('completion')
        try:
            written = upsert_rows(client, payloads, dry_run)
        except Exception as exc:
            written, completion = 0, 'error: upsert %s' % str(exc)[:110]
        results.append({'feed': L.EBIRJA_FEEDS[source_key], 'completion': completion,
                        'complete': bool(res.get('complete')) and written == len(payloads), 'rows': written,
                        'pages': res.get('pages_collected')})
    return results


def _pending_details(client, limit):
    # type: (Any, int) -> List[Dict[str, Any]]
    """Строки без деталей, новые первыми: сначала ebirja, потом «печатные» прямые."""
    cols = 'feed,business_id,category,details_fetched_at,awarded_on'
    rows = client.table(TABLE).select(cols).like('feed', 'ebirja_%').is_('details_fetched_at', 'null') \
        .order('awarded_on', desc=True).limit(min(limit, 1000)).execute().data or []
    if len(rows) < limit:
        flt = ','.join('category.ilike.%s' % p for p in _DIRECT_DETAIL_ILIKE)
        rows += client.table(TABLE).select(cols).eq('feed', 'direct').is_('details_fetched_at', 'null') \
            .or_(flt).order('awarded_on', desc=True).limit(min(limit - len(rows), 1000)).execute().data or []
    return [r for r in rows if L.needs_details(r)][:limit]


def _fetch_details(row, get):
    # type: (Dict[str, Any], Callable[..., Any]) -> Dict[str, Any]
    feed = row['feed']
    if feed == 'direct':
        response = get(DIRECT_DETAIL_URL % row['business_id'], timeout=40)
        response.raise_for_status()
        return L.direct_details(response.json() or {})
    source_key = feed[len('ebirja_'):]
    response = get(EBIRJA_CARD_ENDPOINTS[source_key], params={'id': row['business_id']}, timeout=40,
                   headers={'Accept': 'application/json'})
    response.raise_for_status()
    payload = response.json() or {}
    card = payload.get('result') if isinstance(payload.get('result'), dict) else payload
    return L.ebirja_card_details(source_key, card)


def collect_details(client, limit, dry_run, deadline, pause, get=None):
    # type: (Any, int, bool, float, float, Optional[Callable[..., Any]]) -> Dict[str, Any]
    get = get or httpx.get
    pending = _pending_details(client, limit) if limit > 0 else []
    done = failed = streak = 0
    buffer = {}  # type: Dict[str, List[Dict[str, Any]]]
    completion = 'done'
    for row in pending:
        if time.time() >= deadline:
            completion = 'time_budget'
            break
        try:
            details = scrub(_fetch_details(row, get))
            streak = 0
        except Exception as exc:
            failed += 1
            streak += 1
            logger.warning('details %s/%s: %s', row['feed'], row['business_id'], str(exc)[:100])
            if streak >= 5:
                completion = 'error_streak'
                break
            time.sleep(pause)
            continue
        now = datetime.now(timezone.utc).isoformat()
        buffer.setdefault(row['feed'], []).append(L.details_payload(row, details, now))
        done += 1
        if sum(len(v) for v in buffer.values()) >= 100:
            for rows in buffer.values():
                upsert_rows(client, rows, dry_run)
            buffer = {}
        time.sleep(pause)
    for rows in buffer.values():
        upsert_rows(client, rows, dry_run)
    return {'feed': 'details', 'completion': completion, 'complete': completion == 'done',
            'rows': done, 'failed': failed, 'pending_seen': len(pending)}


def fix_branch_inns(client, dry_run, pause, get=None):
    # type: (Any, bool, float, Optional[Callable[..., Any]]) -> Dict[str, Any]
    """Проставить ИНН головной компании строкам ebirja, чей филиал пришёл 14 цифрами.

    До 08.10 такой tin отбрасывался (L.buyer_inn_of). Перечитывать все карточки
    незачем: заказчик — одно имя, поэтому карточка на имя, а если строк с ним
    больше одной — вторая для сверки; разошлись — имя пропускаем и называем.
    ИП (YATT) не трогаем: их 14 цифр — ПИНФЛ.
    """
    get = get or httpx.get
    rows, last = [], 0  # type: List[Dict[str, Any]], int
    while True:
        page = client.table(TABLE).select('id,feed,business_id,buyer_name').like('feed', 'ebirja_%') \
            .is_('buyer_inn', 'null').not_.is_('details_fetched_at', 'null').is_('deleted_at', 'null') \
            .gt('id', last).order('id').limit(1000).execute().data or []
        rows.extend(page)
        if len(page) < 1000:
            break
        last = page[-1]['id']
    by_name = {}  # type: Dict[Tuple[str, str], List[Dict[str, Any]]]
    for r in rows:
        if r.get('buyer_name') and not L.is_individual(r['buyer_name']):
            by_name.setdefault((r['feed'], r['buyer_name']), []).append(r)
    fixed, updated, conflicts, unresolved = [], 0, [], []
    for (feed, name), items in sorted(by_name.items()):
        inns = set()
        for r in items[:2]:
            try:
                inns.add(_fetch_details(r, get).get('buyer_inn'))
            except Exception as exc:
                logger.warning('branch inn %s/%s: %s', feed, r['business_id'], str(exc)[:100])
                inns.add(None)
            time.sleep(pause)
        if len(inns) != 1 or None in inns:
            (conflicts if len(inns - {None}) > 1 else unresolved).append(name)
            continue
        inn = inns.pop()
        fixed.append({'feed': feed, 'name': name, 'inn': inn, 'rows': len(items)})
        if not dry_run:
            res = client.table(TABLE).update({'buyer_inn': inn, 'updated_at': datetime.now(timezone.utc).isoformat()}) \
                .eq('feed', feed).eq('buyer_name', name).is_('buyer_inn', 'null').execute()
            updated += len(res.data or [])
    return {'feed': 'branch_inn', 'names': len(by_name), 'fixed_names': len(fixed), 'rows_updated': updated,
            'rows_matched': sum(f['rows'] for f in fixed), 'conflicts': conflicts, 'unresolved': unresolved,
            'fixed': fixed}


def main():
    # type: () -> int
    parser = argparse.ArgumentParser(description='Сбор журнала закупок по ИНН заказчика')
    parser.add_argument('--feed', choices=['deals', 'direct', 'civil', 'ebirja', 'all', 'none'], default='all')
    parser.add_argument('--since', default=None, help='YYYY-MM-DD, по умолчанию — 24 месяца назад')
    parser.add_argument('--incremental', action='store_true', help='последние %d дней, без состояния' % INCREMENTAL_DAYS)
    parser.add_argument('--details', type=int, default=0, help='сколько карточек/деталей прочитать')
    parser.add_argument('--max-minutes', type=float, default=90)
    parser.add_argument('--pause', type=float, default=2.0, help='секунд между запросами')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--fix-branch-inn', action='store_true',
                        help='проставить ИНН головной компании строкам ebirja с филиальным tin (разово)')
    args = parser.parse_args()
    if args.fix_branch_inn:
        print(json.dumps(fix_branch_inns(_client(), args.dry_run, args.pause), ensure_ascii=False))
        return 0

    today = date.today()
    if args.incremental:
        since = today - timedelta(days=INCREMENTAL_DAYS)
    elif args.since:
        since = date.fromisoformat(args.since)
    else:
        since = today.replace(year=today.year - 2)
    deadline = time.time() + args.max_minutes * 60
    # Сухой прогон не пишет, но очередь деталей читает из базы.
    client = _client() if (not args.dry_run or args.details) else None

    feeds = [] if args.feed == 'none' else (['deals', 'direct', 'civil', 'ebirja'] if args.feed == 'all'
                                            else [args.feed])
    results = []  # type: List[Dict[str, Any]]
    for feed in feeds:
        if feed == 'ebirja':
            results.extend(collect_ebirja(since, client, args.dry_run))
        else:
            results.append(collect_uzex(feed, since, client, args.dry_run, deadline, args.pause,
                                        args.incremental))
        logger.info('%s', json.dumps(results[-1], ensure_ascii=False))
    if args.details:
        results.append(collect_details(client, args.details, args.dry_run, deadline, args.pause))
        logger.info('%s', json.dumps(results[-1], ensure_ascii=False))

    broken = [r for r in results if str(r.get('completion', '')).startswith(('error', 'schema'))]
    if broken and args.incremental:
        from crawler.scripts.fetch_ebirja_contracts import _send_telegram_alert
        _send_telegram_alert('<b>Журнал закупок</b>\nЛенты не собраны: %s' % ', '.join(
            '%s (%s)' % (r['feed'], str(r['completion'])[:60]) for r in broken))
    print(json.dumps({'since': since.isoformat(), 'results': results}, ensure_ascii=False))
    return 1 if broken else 0


if __name__ == '__main__':
    sys.exit(main())
