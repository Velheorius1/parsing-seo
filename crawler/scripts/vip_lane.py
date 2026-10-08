"""⭐-полоса топ-100: индекс, режим, бэктест на истории, разбор тени (фаза 6).

  python3 -m crawler.scripts.vip_lane build                # data/private/vip_index.json из реестра + журнала
  python3 -m crawler.scripts.vip_lane mode shadow          # off | shadow | digest | push
  python3 -m crawler.scripts.vip_lane backtest --days 60   # что полоса дала бы на истории
  python3 -m crawler.scripts.vip_lane shadow-report        # что набралось в тени

Бэктест (план: «сначала прогон на 60 днях истории»). Берёт лоты заказчиков
топ-100 за окно — по ИНН в extra_info и по имени заказчика, под которым этот ИНН
покупал в журнале, — и прогоняет их через тот же prefilter с индексом. Лоты,
прошедшие только по ⭐, судит AI (тот же replay, что у детектора). Итог —
сколько алертов в неделю добавит полоса и какие именно; по делу ли они, решает
человек, читая список. Имя ищется точной строкой из журнала, а живое
сопоставление нормализует кавычки и правовые формы — живая полоса поймает не
меньше, бэктест — оценка снизу.
"""
import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from crawler.core import customer_registry as CR
from crawler.core import vip_lane as V

INN_BATCH = 50
# Байт имён в одном запросе после URL-кодирования. Шлюз возвращает адрес запроса
# в заголовке ОТВЕТА, а nginx перед ним держит буфер ~4 КБ: кириллица кодируется
# ×6, и уже 2,5 КБ имён давали «upstream sent too big header» → 502 (08.10).
URL_BUDGET = 1000
PAGE = 1000
LOT_PAGE = 200      # строки лотов тяжёлые (extra_info предквалификаций со списком позиций): 1000 за раз — 502 шлюза
AI_CAP = 300
_LOT_FIELDS = ('id,external_id,source,source_url,title,organization,search_text,price,currency,deadline,'
               'message_type,extra_info,bid_count,status,collected_at,created_at,alert_seq')


def _client():
    # type: () -> Any
    from crawler.core.db import _get_client
    return _get_client()


def _pages(build, size=PAGE):
    # type: (Any, int) -> List[Dict[str, Any]]
    from crawler.core.db import query_with_retry
    rows, offset = [], 0  # type: List[Dict[str, Any]], int
    while True:
        page = query_with_retry(lambda: build().range(offset, offset + size - 1).execute(),
                                label='vip-lane').data or []
        rows.extend(page)
        if len(page) < size:
            return rows
        offset += size


def ledger_rows(client, registry):
    # type: (Any, Dict[str, Any]) -> List[Dict[str, Any]]
    inns = sorted(CR.by_inn(registry))
    out = []  # type: List[Dict[str, Any]]
    for i in range(0, len(inns), INN_BATCH):
        batch = inns[i:i + INN_BATCH]
        out.extend(_pages(lambda batch=batch: client.table('purchase_ledger')
                          .select('id,buyer_inn,buyer_name,winner_name,amount_uzs,profile')
                          .in_('buyer_inn', batch).is_('deleted_at', 'null').order('id')))
    return out


def build(client, registry):
    # type: (Any, Dict[str, Any]) -> Dict[str, Any]
    from crawler.core.outcome import is_our_win
    rows = ledger_rows(client, registry)
    index = V.build_index(registry, rows, date.today().isoformat(), is_ours=is_our_win)
    index['names'] = sorted({r['buyer_name'] for r in rows if r.get('buyer_name')})   # для бэктеста
    return index


def write_index(index):
    # type: (Dict[str, Any]) -> None
    V.INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = V.INDEX_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding='utf-8')
    tmp.replace(V.INDEX_PATH)


def window_lots(client, index, since):
    # type: (Any, Dict[str, Any], str) -> Tuple[List[Dict[str, Any]], int]
    """Лоты окна по ИНН и по точному имени из журнала; дубли — по id. -> (лоты, запросов)."""
    by_id, queries = {}, 0  # type: Dict[str, Dict[str, Any]], int
    inns = sorted(index['inns'])
    for key in V.INN_KEYS[:2]:
        for i in range(0, len(inns), INN_BATCH):
            batch = inns[i:i + INN_BATCH]
            for r in _pages(lambda batch=batch, key=key: client.table('tenders').select(_LOT_FIELDS)
                            .gte('created_at', since).in_('extra_info->>%s' % key, batch).order('id'), LOT_PAGE):
                by_id[r['id']] = r
            queries += 1
    for batch in name_batches(index.get('names') or []):
        for r in _pages(lambda batch=batch: client.table('tenders').select(_LOT_FIELDS)
                        .gte('created_at', since).in_('organization', batch).order('id'), LOT_PAGE):
            by_id[r['id']] = r
        queries += 1
    return [r for r in by_id.values() if V.match(_t(r), index)], queries


def name_batches(names, budget=URL_BUDGET):
    # type: (List[str], int) -> List[List[str]]
    from urllib.parse import quote
    out, cur, size = [], [], 0  # type: List[List[str]], List[str], int
    for name in names:
        cost = len(quote(name)) + 3
        if cur and size + cost > budget:
            out.append(cur)
            cur, size = [], 0
        cur.append(name)
        size += cost
    if cur:
        out.append(cur)
    return out


def _t(row):
    # type: (Dict[str, Any]) -> Any
    from crawler.core.tender_rows import row_to_raw_tender
    return row_to_raw_tender(row)


async def _replay(rows, use_ai, index):
    # type: (List[Dict[str, Any]], bool, Dict[str, Any]) -> List[Any]
    from crawler.scripts.replay import replay_tenders
    tenders = [_t(r) for r in rows]
    first_seen = {t.external_id: r.get('created_at') for t, r in zip(tenders, rows)}
    return await replay_tenders(tenders, use_ai=use_ai, as_of='collected_at', collected_at=first_seen,
                                vip_index=index)


async def backtest(client, index, days, ai_cap):
    # type: (Any, Dict[str, Any], int, int) -> Dict[str, Any]
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    lots, queries = window_lots(client, index, since)
    verdicts = await _replay(lots, False, index)
    stages = Counter(v.dropped_at_stage or 'passed' for v in verdicts)
    vip_only = [r for r, v in zip(lots, verdicts) if v.passed_prefilter and V.is_vip_kw(v.matched_kw)]
    by_kw = [r for r, v in zip(lots, verdicts) if v.passed_prefilter and not V.is_vip_kw(v.matched_kw)]
    sample = vip_only if len(vip_only) <= ai_cap else random.Random(days).sample(vip_only, ai_cap)
    judged = await _replay(sample, True, index) if sample else []
    ok = [(r, v) for r, v in zip(sample, judged) if not v.ai_error]
    accepted = [(r, v) for r, v in ok if v.delivered]
    share = len(accepted) / float(len(ok)) if ok else 0.0
    weeks = days / 7.0

    def item(r, v=None):
        entity = index['entities'].get(V.match(_t(r), index) or '', {})
        return {'entity': entity.get('name'), 'rank': entity.get('rank'), 'source': r.get('source'),
                'title': (r.get('title') or '')[:160], 'organization': r.get('organization'),
                'price': r.get('price'), 'created_at': str(r.get('created_at'))[:10],
                'alerted': r.get('alert_seq') is not None,
                'ai': None if v is None else {'score': v.ai_score, 'category': v.ai_category, 'route': v.route}}
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(), 'days': days, 'queries': queries,
        'lots_of_top100': len(lots), 'stages': dict(stages), 'vip_only_passed': len(vip_only),
        'keyword_passed_get_star': len(by_kw), 'judged': len(ok), 'ai_errors': len(sample) - len(ok),
        'accepted': len(accepted),
        'est_vip_alerts_week': round(len(vip_only) * share / weeks, 1),
        'est_star_alerts_week': round(len(by_kw) / weeks, 1),
        'by_entity': Counter(item(r)['entity'] for r, _ in accepted).most_common(),
        'accepted_items': [item(r, v) for r, v in accepted],
        'rejected_sample': [item(r, v) for r, v in ok if not v.delivered][:30],
    }


def shadow_report(days):
    # type: (int) -> Dict[str, Any]
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = []
    try:
        with open(str(V.SHADOW_LOG), encoding='utf-8') as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except IOError:
        pass
    rows = [r for r in rows if str(r.get('at')) >= since]
    return {'days': days, 'lots': len(rows), 'by_entity': Counter(r.get('entity') for r in rows).most_common(10),
            'by_source': Counter(r.get('source') for r in rows).most_common(10)}


def main(argv=None):
    # type: (Any) -> int
    parser = argparse.ArgumentParser(description='⭐-полоса топ-100 (фаза 6)')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('build')
    m = sub.add_parser('mode')
    m.add_argument('value', nargs='?', choices=V.MODES)
    m.add_argument('--by', default='Данияр')
    b = sub.add_parser('backtest')
    b.add_argument('--days', type=int, default=60)
    b.add_argument('--ai-calls', type=int, default=AI_CAP)
    s = sub.add_parser('shadow-report')
    s.add_argument('--days', type=int, default=7)
    args = parser.parse_args(argv)
    if args.cmd == 'mode':
        if args.value:
            V.set_mode(args.value, args.by)
        print(V.mode())
        return 0
    if args.cmd == 'shadow-report':
        print(json.dumps(shadow_report(args.days), ensure_ascii=False))
        return 0
    client, registry = _client(), CR.load()
    index = build(client, registry)
    if args.cmd == 'build':
        write_index(index)
        print(json.dumps({'entities': len(index['entities']), 'inns': len(index['inns']),
                          'aliases': len(index['aliases']), 'with_winners': sum(
                              1 for e in index['entities'].values() if e['winners'])}, ensure_ascii=False))
        return 0
    report = asyncio.run(backtest(client, index, args.days, args.ai_calls))
    out = CR.REGISTRY_PATH.parent / ('vip_backtest_%s.json' % date.today().isoformat())
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('accepted_items', 'rejected_sample')},
                     ensure_ascii=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
