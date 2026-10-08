#!/usr/bin/env python3
"""Детектор пропусков: кто из топ-100 купил наше, а мы не узнали (фаза 4).

Берёт из журнала закупок (purchase_ledger) покупки нашего профиля (полиграфия,
мерч) заказчиков реестра от 20 млн и для каждой отвечает, где были мы. Связь
с нашим лотом и стадия отсева — те же, что у сводки конкурентов
(competitor_wins: лот по ссылке /lot/ или по display_id ВМК-69, алерт, срок,
прогон через сегодняшний гейт на дату первого появления лота).

Виды (miss_type):
  WON                 — выиграли мы;
  KNOWN_LOST          — алерт до срока был, выиграл другой: не пропуск;
  KNOWN_REJECTED      — алерт был, человек пометил «мимо/реклама»;
  A_filter            — лот у нас в базе, алерта не было; root_stage — стадия
                        гейта, delivery_loss (AI сказал «наш», алерт потерялся),
                        passes_today (сегодня гейт пропускает) или undecided;
  A_not_collected     — площадку собираем, а этот лот — нет;
  A_late              — алерт пришёл после срока подачи;
  B_no_announcement   — прямой договор UZEX или э-магазин ebirja: объявления
                        нет, выиграть можно только своим каталогом на площадке;
  C_uncrawled_platform — объявлений площадки у нас нет или они не сшиваются
                        с договором (тендеры, отборы и аукционы ebirja);
  PRE_COVERAGE        — закупка раньше, чем мы начали собирать эти лоты: не
                        пропуск, в полноту не входит.
D (собственные сайты заказчиков) из журнала не видно: нужен внешний сигнал.

Пишет miss_type / root_stage / analysed_at в purchase_ledger, сводку полноты
по заказчикам — в data/private/customer_miss_<дата>.json и строку истории в
data/private/customer_recall_history.jsonl.

  python3 -m crawler.scripts.customer_miss                    # AI до 80 вызовов
  python3 -m crawler.scripts.customer_miss --no-ai --dry-run
  python3 -m crawler.scripts.customer_miss --check civil:17614,deals:176085
"""
import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from crawler.core import competitor_wins as CW
from crawler.core import customer_registry as CR
from crawler.core.competitor_audit import THRESHOLD_UZS
from crawler.core.outcome import is_our_win
from crawler.core.purchase_ledger import PURCHASE_STATUSES
from crawler.scripts.customer_rank import PRIVATE_DIR

KNOWN = ('WON', 'KNOWN_LOST', 'KNOWN_REJECTED')
MISSES = ('A_filter', 'A_not_collected', 'A_late', 'B_no_announcement', 'C_uncrawled_platform')
EXCLUDED = ('PRE_COVERAGE',)
# Лот объявляют за 2–4 недели до договора. Договор в первые 3 недели после
# начала нашего сбора без нашей строки лота — лот, скорее всего, вышел раньше.
ANNOUNCE_LEAD_DAYS = 21
AI_CAP = 80
_COLS = ('feed,business_id,buyer_inn,buyer_name,winner_inn,winner_name,subject,amount_uzs,awarded_on,'
         'status,profile,lot_key,source_url,miss_type,root_stage')


def classify(purchase, lot_rows, labels, coverage, after_deadline):
    # type: (Dict[str, Any], List[Dict[str, Any]], Dict[int, str], Dict[str, Optional[date]], Any) -> Tuple[str, Optional[str]]
    """(miss_type, root_stage). root_stage None у A_filter — нужна стадия гейта (replay)."""
    feed = purchase.get('feed') or ''
    if is_our_win(purchase.get('winner_name')):
        return 'WON', None
    if feed == 'direct':
        return 'B_no_announcement', 'прямой договор UZEX'
    if feed == 'ebirja_shop':
        return 'B_no_announcement', 'э-магазин ebirja'
    if feed.startswith('ebirja_'):
        return 'C_uncrawled_platform', '%s ebirja: объявления не сшиваются с договором' % feed[7:]
    if feed not in ('deals', 'civil'):
        return 'C_uncrawled_platform', feed
    status, first = CW.alert_status(lot_rows, labels)
    if status == CW.STATUS_NOT_COLLECTED:
        start = coverage.get(feed)
        awarded = date.fromisoformat(str(purchase['awarded_on'])[:10])
        if start is None or awarded < start + timedelta(days=ANNOUNCE_LEAD_DAYS):
            return 'PRE_COVERAGE', 'лоты %s собираем с %s' % (feed, start)
        return 'A_not_collected', None
    if status == CW.STATUS_REJECTED_BY_HUMAN:
        return 'KNOWN_REJECTED', 'человек: мимо/реклама'
    if status == CW.STATUS_ALERTED:
        if after_deadline(first):
            return 'A_late', 'алерт после срока'
        return 'KNOWN_LOST', None
    if CW.missed_is_delivery_loss(lot_rows):
        return 'A_filter', 'delivery_loss'
    return 'A_filter', None


def recall(rows, registry):
    # type: (List[Dict[str, Any]], Dict[str, Any]) -> List[Dict[str, Any]]
    """Полнота по заказчику: доля «знали» среди покупок, по числу и по деньгам.
    Полная — всех покупок; «по объявленным» — без вида B (там объявления нет)."""
    by_inn = CR.by_inn(registry)
    agg = defaultdict(lambda: {'known': 0, 'miss': 0, 'known_uzs': 0.0, 'miss_uzs': 0.0,
                               'announced_known': 0, 'announced_miss': 0, 'types': defaultdict(int)})
    names = {}
    for row in rows:
        kind = row.get('miss_type')
        if kind not in KNOWN and kind not in MISSES:
            continue
        entity = by_inn.get(row['buyer_inn'])
        key = entity['id'] if entity else row['buyer_inn']
        names[key] = entity['name'] if entity else row.get('buyer_name')
        a = agg[key]
        amount = float(row.get('amount_uzs') or 0)
        a['types'][kind] += 1
        side = 'known' if kind in KNOWN else 'miss'
        a[side] += 1
        a[side + '_uzs'] += amount
        if kind != 'B_no_announcement':
            a['announced_' + side] += 1
    out = []
    for key, a in agg.items():
        total, total_uzs = a['known'] + a['miss'], a['known_uzs'] + a['miss_uzs']
        announced = a['announced_known'] + a['announced_miss']
        out.append({'id': key, 'name': names[key], 'purchases': total,
                    'recall': round(a['known'] / total, 3) if total else None,
                    'recall_uzs': round(a['known_uzs'] / total_uzs, 3) if total_uzs else None,
                    'recall_announced': round(a['announced_known'] / announced, 3) if announced else None,
                    'miss_uzs': a['miss_uzs'], 'types': dict(a['types'])})
    out.sort(key=lambda r: -r['miss_uzs'])
    return out


# ── чтение и запись ──────────────────────────────────────────────────────────

def _client():
    # type: () -> Any
    from crawler.core.db import _get_client
    return _get_client()


def coverage(client):
    # type: (Any) -> Dict[str, Optional[date]]
    """С какого дня у нас есть строки лотов: из базы, а не руками."""
    def first(sources):
        rows = client.table('tenders').select('created_at').in_('source', list(sources)) \
            .order('created_at').limit(1).execute().data or []
        return date.fromisoformat(rows[0]['created_at'][:10]) if rows else None
    return {'deals': first(CW.LOT_SOURCES), 'civil': first(CW.CIVIL_LOT_SOURCES)}


def candidates(client, registry, since, today):
    # type: (Any, Dict[str, Any], str, date) -> List[Dict[str, Any]]
    """Покупки нашего профиля заказчиков реестра от порога (in_ по 50 ИНН)."""
    inns = sorted(CR.by_inn(registry))
    rows = []  # type: List[Dict[str, Any]]
    for start in range(0, len(inns), 50):
        batch = client.table('purchase_ledger').select(_COLS).in_('buyer_inn', inns[start:start + 50]) \
            .in_('profile', ['poly', 'merch']).gte('awarded_on', since).is_('deleted_at', 'null') \
            .limit(1000).execute().data or []
        if len(batch) >= 1000:
            # PostgREST отдаёт не больше 1000: полнота по усечённой выборке — неправда.
            raise RuntimeError('покупки 50 ИНН упёрлись в потолок 1000 строк — выборка неполна')
        rows.extend(batch)
    return [r for r in rows if r.get('status') in PURCHASE_STATUSES and r.get('amount_uzs') is not None
            and Decimal(str(r['amount_uzs'])) >= THRESHOLD_UZS and str(r['awarded_on']) <= today.isoformat()]


def by_keys(client, keys):
    # type: (Any, List[Tuple[str, str]]) -> List[Dict[str, Any]]
    rows = []  # type: List[Dict[str, Any]]
    for feed, business_id in keys:
        rows.extend(client.table('purchase_ledger').select(_COLS).eq('feed', feed)
                    .eq('business_id', business_id).execute().data or [])
    return rows


async def analyse(client, rows, use_ai, ai_cap):
    # type: (Any, List[Dict[str, Any]], bool, int) -> Dict[str, Any]
    """Проставить строкам miss_type / root_stage на месте; вернуть счёт AI."""
    from crawler.scripts import competitor_wins_weekly as W
    cov = coverage(client)
    deals_urls = sorted({r['source_url'] for r in rows if r['feed'] == 'deals' and r.get('source_url')})
    civil_keys = sorted({r['lot_key'] for r in rows if r['feed'] == 'civil' and r.get('lot_key')})
    lots_by_url = W.fetch_lot_rows(client, deals_urls) if deals_urls else {}
    civil_lots = W.fetch_civil_lot_rows(client, civil_keys) if civil_keys else {}

    def lots_of(row):
        if row['feed'] == 'civil':
            return civil_lots.get(CW.civil_norm_key(row.get('lot_key')), [])
        if row['feed'] == 'deals':
            return lots_by_url.get(row.get('source_url') or '', [])
        return []
    seqs = sorted({int(l['alert_seq']) for r in rows for l in lots_of(r) if l.get('alert_seq') is not None})
    labels = W.fetch_human_labels(client, seqs) if seqs else {}
    need = []
    for row in rows:
        row['miss_type'], row['root_stage'] = classify(row, lots_of(row), labels, cov, W._after_deadline)
        if row['miss_type'] == 'A_filter' and row['root_stage'] is None:
            row['_lot_row'] = CW.pick_lot_row(lots_of(row))
            need.append(row)
    budget = W._Budget(ai_cap if use_ai else 0)
    if need:
        for row, verdict in zip(need, await W._replay([r['_lot_row'] for r in need], use_ai=False)):
            if not verdict.passed_prefilter:
                row['root_stage'] = verdict.dropped_at_stage or 'prefilter'
        # Дорогие первыми: потолок AI съедает хвост мелочи, а не крупные покупки.
        for row in sorted([r for r in need if r['root_stage'] is None], key=lambda r: -float(r['amount_uzs'])):
            verdict = await W._ai(row['_lot_row'], budget)
            if verdict is None:
                row['root_stage'] = 'undecided'
            elif CW.is_profile_verdict(verdict):
                row['root_stage'] = CW.REASON_PASSES_TODAY
            else:
                row['root_stage'] = CW.stage_of(verdict) or 'ai'
    for row in rows:
        row.pop('_lot_row', None)
    return {'coverage': {k: str(v) if v else None for k, v in cov.items()},
            'ai_used': budget.used, 'ai_errors': budget.errors}


def write(client, rows, dry_run):
    # type: (Any, List[Dict[str, Any]], bool) -> int
    from crawler.scripts.purchase_backfill import upsert_rows
    now = datetime.now(timezone.utc).isoformat()
    return upsert_rows(client, [{'feed': r['feed'], 'business_id': r['business_id'], 'miss_type': r['miss_type'],
                                 'root_stage': r['root_stage'], 'analysed_at': now} for r in rows], dry_run)


def main(argv=None):
    # type: (Any) -> int
    parser = argparse.ArgumentParser(description='Пропуски топ-100: купили наше — знали ли мы')
    parser.add_argument('--since', help='YYYY-MM-DD; по умолчанию — начало нашего сбора лотов etender')
    parser.add_argument('--ai-calls', type=int, default=AI_CAP)
    parser.add_argument('--no-ai', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--check', help='feed:business_id,… — разобрать именно эти (без фильтров, без записи)')
    args = parser.parse_args(argv)
    client, today = _client(), date.today()
    registry = CR.load()
    if args.check:
        rows = by_keys(client, [tuple(part.split(':', 1)) for part in args.check.split(',') if ':' in part])
        stats = asyncio.run(analyse(client, rows, not args.no_ai, args.ai_calls))
        for r in rows:
            print('%s:%s %s млн %s | %s — %s | %s' % (r['feed'], r['business_id'], round(float(r['amount_uzs'] or 0) / 1e6),
                                                     r.get('profile'), r['miss_type'], r['root_stage'],
                                                     (r.get('subject') or '')[:60]))
        print(json.dumps(stats, ensure_ascii=False))
        return 0
    since = args.since or str(coverage(client)['deals'] or (today - timedelta(days=365)))
    summary, _rows, _per_customer = run(client, registry, since, today, not args.no_ai, args.ai_calls, args.dry_run)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def run(client, registry, since, today, use_ai=True, ai_calls=AI_CAP, dry_run=False):
    # type: (Any, Dict[str, Any], str, date, bool, int, bool) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]
    """Разобрать покупки, записать вид пропуска и полноту. Зовёт и недельный отчёт.
    -> (сводка, покупки с miss_type/root_stage, полнота по заказчикам)."""
    rows = candidates(client, registry, since, today)
    stats = asyncio.run(analyse(client, rows, use_ai, ai_calls))
    written = write(client, rows, dry_run)
    per_customer = recall(rows, registry)
    known = sum(1 for r in rows if r['miss_type'] in KNOWN)
    missed = sum(1 for r in rows if r['miss_type'] in MISSES)
    announced = [r for r in rows if r['miss_type'] in KNOWN + MISSES and r['miss_type'] != 'B_no_announcement']
    summary = {'generated_at': datetime.now(timezone.utc).isoformat(), 'since': since, 'purchases': len(rows),
               'known': known, 'missed': missed, 'excluded': len(rows) - known - missed,
               'recall': round(known / (known + missed), 3) if known + missed else None,
               'recall_announced': round(known / float(len(announced)), 3) if announced else None,
               'recall_announced_uzs': _share_uzs(announced),
               'types': {k: sum(1 for r in rows if r['miss_type'] == k) for k in KNOWN + MISSES + EXCLUDED},
               'written': written, **stats}
    if not dry_run:
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        (PRIVATE_DIR / ('customer_miss_%s.json' % today.isoformat())).write_text(json.dumps(
            dict(summary, customers=per_customer, purchases_detail=[
                {k: r.get(k) for k in ('feed', 'business_id', 'buyer_name', 'winner_name', 'subject', 'amount_uzs',
                                       'awarded_on', 'miss_type', 'root_stage', 'source_url')} for r in rows]),
            ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        with open(str(PRIVATE_DIR / 'customer_recall_history.jsonl'), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(dict(summary, customers=[{k: c[k] for k in ('id', 'recall', 'recall_uzs', 'purchases')}
                                                         for c in per_customer]), ensure_ascii=False) + '\n')
    return summary, rows, per_customer


def _share_uzs(rows):
    # type: (List[Dict[str, Any]]) -> Optional[float]
    """Доля «знали» по деньгам."""
    total = sum(float(r.get('amount_uzs') or 0) for r in rows)
    known = sum(float(r.get('amount_uzs') or 0) for r in rows if r['miss_type'] in KNOWN)
    return round(known / total, 3) if total else None


if __name__ == '__main__':
    sys.exit(main())
