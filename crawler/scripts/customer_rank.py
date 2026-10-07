#!/usr/bin/env python3
"""Топ заказчиков нашей продукции по журналу закупок (purchase_ledger).

Балл = траты на полиграфию и мерч за последние 12 мес ×2 + за месяцы 13–24
(решение Данияра 07.10.2026). Группировка — по ИНН заказчика; строки без ИНН
(карточка ebirja ещё не прочитана) в рейтинг не входят и показаны в охвате.
Отказ и расторжение — не покупка (purchase_ledger.PURCHASE_STATUSES).

Результат — data/private/customer_rank_<дата>.json и .html (репозиторий
публичный: список заказчиков в git не кладём). --tg шлёт HTML документом.

  python3 -m crawler.scripts.customer_rank --top 100
  python3 -m crawler.scripts.customer_rank --top 100 --tg
"""
import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from crawler.core import purchase_ledger as L
from crawler.core import purchase_profile as P
from crawler.scripts.purchase_backfill import TABLE

PRIVATE_DIR = Path(__file__).resolve().parents[2] / 'data' / 'private'
_COLS = ('id,feed,business_id,buyer_inn,buyer_name,buyer_type,winner_inn,winner_name,subject,'
         'amount_uzs,awarded_on,status,profile,source_url')
FEED_RU = {'deals': 'сделки etender', 'direct': 'прямые закупки UZEX', 'civil': 'итоги ВМК-69',
           'ebirja_shop': 'ebirja магазин', 'ebirja_auction': 'ebirja аукцион',
           'ebirja_tender': 'ebirja тендер', 'ebirja_selection': 'ebirja отбор'}


def _client():
    # type: () -> Any
    from crawler.core.db import _get_client
    return _get_client()


def profile_rows(client, since):
    # type: (Any, str) -> List[Dict[str, Any]]
    rows = []  # type: List[Dict[str, Any]]
    last_id = 0
    while True:
        batch = client.table(TABLE).select(_COLS).in_('profile', ['poly', 'merch']).gte('awarded_on', since) \
            .is_('deleted_at', 'null').gt('id', last_id).order('id').limit(1000).execute().data or []
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        last_id = batch[-1]['id']


def coverage(client):
    # type: (Any) -> List[Dict[str, Any]]
    """Охват по лентам: сколько строк, с какой даты, сколько без ИНН / без оценки."""
    out = []
    for feed in list(FEED_RU):
        def count(**flt):
            q = client.table(TABLE).select('id', count='exact').eq('feed', feed)
            for key, value in flt.items():
                q = q.is_(key, 'null') if value is None else q.eq(key, value)
            return q.limit(1).execute().count or 0
        total = count()
        if not total:
            out.append({'feed': feed, 'total': 0})
            continue
        oldest = client.table(TABLE).select('awarded_on').eq('feed', feed).order('awarded_on') \
            .limit(1).execute().data
        out.append({'feed': feed, 'total': total, 'since': (oldest or [{}])[0].get('awarded_on'),
                    'no_inn': count(buyer_inn=None), 'unlabeled': count(profile=None)})
    return out


def rank(rows, today, top):
    # type: (List[Dict[str, Any]], date, int) -> Dict[str, Any]
    cut12 = (today - timedelta(days=365)).isoformat()
    groups = defaultdict(list)  # type: Dict[str, List[Dict[str, Any]]]
    skipped = Counter()  # type: Counter
    for row in rows:
        if row.get('status') not in L.PURCHASE_STATUSES:
            skipped['status:%s' % row.get('status')] += 1
            continue
        if row.get('amount_uzs') is None:
            skipped['не в сумах'] += 1
            continue
        if not row.get('buyer_inn'):
            skipped['без ИНН'] += 1
            continue
        if not row.get('awarded_on') or row['awarded_on'] > today.isoformat():
            skipped['дата в будущем'] += 1
            continue
        groups[row['buyer_inn']].append(row)
    entities = []
    for inn, items in groups.items():
        spend12 = sum(Decimal(str(r['amount_uzs'])) for r in items if r['awarded_on'] >= cut12)
        spend24 = sum(Decimal(str(r['amount_uzs'])) for r in items if r['awarded_on'] < cut12)
        total = spend12 + spend24
        names = Counter(r.get('buyer_name') for r in items if r.get('buyer_name'))
        name = names.most_common(1)[0][0] if names else inn
        types = Counter(r.get('buyer_type') for r in items if r.get('buyer_type'))
        winners = defaultdict(Decimal)  # type: Dict[str, Decimal]
        for r in items:
            winners[r.get('winner_name') or r.get('winner_inn') or '?'] += Decimal(str(r['amount_uzs']))
        biggest = max(items, key=lambda r: Decimal(str(r['amount_uzs'])))
        entities.append({
            'inn': inn, 'name': name, 'segment': P.segment(name, types.most_common(1)[0][0] if types else None),
            'score': float(spend12 * 2 + spend24), 'spend12': float(spend12), 'spend24': float(spend24),
            'purchases': len(items), 'poly': sum(1 for r in items if r['profile'] == 'poly'),
            'merch': sum(1 for r in items if r['profile'] == 'merch'),
            'biggest_share': float(Decimal(str(biggest['amount_uzs'])) / total) if total else 0.0,
            'biggest_subject': (biggest.get('subject') or '')[:140],
            'winners': [(w, float(s)) for w, s in sorted(winners.items(), key=lambda kv: -kv[1])[:3]],
            'feeds': sorted({r['feed'] for r in items}),
            'last': max(r['awarded_on'] for r in items),
            'examples': [(r.get('subject') or '')[:110] for r in
                         sorted(items, key=lambda r: -Decimal(str(r['amount_uzs'])))[:3]],
        })
    entities.sort(key=lambda e: -e['score'])
    return {'entities': entities[:top], 'total_entities': len(entities), 'skipped': dict(skipped)}


def _mln(value):
    # type: (float) -> str
    return '{:,.0f}'.format(value / 1e6).replace(',', ' ')


def judge_metrics(path=None):
    # type: (Optional[Path]) -> Dict[str, Any]
    """Последний замер судьи на отложенной выборке (поле metrics эталона)."""
    from crawler.scripts.purchase_classify import GOLDEN
    try:
        return json.loads(Path(path or GOLDEN).read_text(encoding='utf-8')).get('metrics') or {}
    except (OSError, ValueError):
        return {}


def status_line(cov, metrics):
    # type: (List[Dict[str, Any]], Dict[str, Any]) -> str
    """Чему верить в списке: замер судьи и сколько договоров ещё не учтено."""
    if metrics.get('prompt') == P.PROMPT_VERSION:
        judge = 'судья профиля: точность %s, полнота %s на отложенной выборке %s предметов' % (
            str(metrics.get('precision')).replace('.', ','), str(metrics.get('recall')).replace('.', ','),
            metrics.get('items'))
    else:
        judge = 'судья профиля не перемерен после смены промпта (%s)' % P.PROMPT_VERSION
    unlabeled = sum(c.get('unlabeled') or 0 for c in cov)
    no_inn = sum(c.get('no_inn') or 0 for c in cov)
    if unlabeled or no_inn:
        return '%s · черновик: %d договоров ещё без оценки, %d без ИНН заказчика — см. охват' % (
            judge, unlabeled, no_inn)
    return judge


def render_html(result, cov, today, metrics=None):
    # type: (Dict[str, Any], List[Dict[str, Any]], date, Optional[Dict[str, Any]]) -> str
    esc = html.escape
    rows = []
    for i, e in enumerate(result['entities'], 1):
        winners = '<br>'.join('%s — %s' % (esc(w[:40]), _mln(s)) for w, s in e['winners'])
        rows.append(
            '<tr><td class="n">%d</td><td><b>%s</b><div class="m">ИНН %s · %s</div>'
            '<div class="m">%s</div></td><td class="n">%s</td><td class="n">%s</td><td class="n">%d'
            '<div class="m">полигр. %d · мерч %d</div></td><td class="n">%d%%<div class="m">%s</div></td>'
            '<td class="w">%s</td><td class="n">%s</td></tr>' % (
                i, esc(e['name']), e['inn'], esc(e['segment']),
                esc(' · '.join(e['examples'][:2])), _mln(e['spend12']), _mln(e['spend24']), e['purchases'],
                e['poly'], e['merch'], round(e['biggest_share'] * 100), esc(e['biggest_subject'][:70]),
                winners, e['last']))
    cov_rows = ''.join(
        '<tr><td>%s</td><td class="n">%s</td><td class="n">%s</td><td class="n">%s</td><td class="n">%s</td></tr>' % (
            esc(FEED_RU.get(c['feed'], c['feed'])), c.get('total', 0), c.get('since') or '—',
            c.get('no_inn', '—'), c.get('unlabeled', '—')) for c in cov)
    skipped = ', '.join('%s: %d' % (esc(k), v) for k, v in sorted(result['skipped'].items())) or 'нет'
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Топ заказчиков</title>
<style>
:root{--bg:#fbfaf7;--fg:#1c1b19;--muted:#6b6862;--line:#e4e1da;--head:#f1eee8}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe7;--muted:#9c9a94;--line:#2c2b28;--head:#1f1e1b}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:16px}
h1{font-size:20px;margin:0 0 4px}p{color:var(--muted);margin:4px 0 14px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%%;min-width:900px}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;vertical-align:top;text-align:left}
th{background:var(--head);font-weight:600;font-size:12px;position:sticky;top:0}
.n{text-align:right;white-space:nowrap}.m{color:var(--muted);font-size:12px}.w{font-size:12px}
h2{font-size:16px;margin:24px 0 6px}
</style></head><body>
<h1>Топ-%d заказчиков полиграфии и мерча</h1>
<p>%s · балл = траты за 12 мес ×2 + за 13–24 мес · суммы в млн сум · заказчиков с покупками: %d</p>
<p>%s</p>
<div class="wrap"><table><tr><th>№</th><th>Заказчик</th><th class="n">12 мес</th><th class="n">13–24 мес</th>
<th class="n">Покупок</th><th class="n">Крупнейшая</th><th>Кто выигрывал</th><th class="n">Последняя</th></tr>
%s</table></div>
<h2>Охват лент</h2><p>Не учтено: %s</p>
<div class="wrap"><table style="min-width:520px"><tr><th>Лента</th><th class="n">Договоров</th><th class="n">С даты</th>
<th class="n">Без ИНН</th><th class="n">Без оценки</th></tr>%s</table></div>
</body></html>""" % (len(result['entities']), today.isoformat(), result['total_entities'],
                     esc(status_line(cov, metrics if metrics is not None else judge_metrics())), ''.join(rows),
                     skipped, cov_rows)


def send_document(path, caption):
    # type: (Path, str) -> bool
    import httpx
    from crawler.config.settings import settings
    with open(str(path), 'rb') as fh:
        response = httpx.post('https://api.telegram.org/bot%s/sendDocument' % settings.telegram_bot_token,
                              data={'chat_id': settings.telegram_alert_chat_id, 'caption': caption[:1000],
                                    'parse_mode': 'HTML', 'protect_content': 'true'},
                              files={'document': (path.name, fh, 'text/html')}, timeout=60)
    return response.status_code == 200 and bool(response.json().get('ok'))


def main():
    # type: () -> int
    parser = argparse.ArgumentParser(description='Топ заказчиков по журналу закупок')
    parser.add_argument('--top', type=int, default=100)
    parser.add_argument('--months', type=int, default=24)
    parser.add_argument('--tg', action='store_true', help='прислать HTML документом в чат алертов')
    args = parser.parse_args()
    today = date.today()
    since = (today - timedelta(days=round(args.months * 30.44))).isoformat()
    client = _client()
    result = rank(profile_rows(client, since), today, args.top)
    cov = coverage(client)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = today.isoformat()
    json_path = PRIVATE_DIR / ('customer_rank_%s.json' % stamp)
    html_path = PRIVATE_DIR / ('customer_rank_%s.html' % stamp)
    json_path.write_text(json.dumps({'generated_at': datetime.now(timezone.utc).isoformat(), 'since': since,
                                     'coverage': cov, **result}, ensure_ascii=False, indent=1), encoding='utf-8')
    html_path.write_text(render_html(result, cov, today), encoding='utf-8')
    print(json.dumps({'entities': result['total_entities'], 'top': len(result['entities']),
                      'skipped': result['skipped'], 'html': str(html_path)}, ensure_ascii=False))
    if args.tg:
        ok = send_document(html_path, '<b>Топ заказчиков полиграфии и мерча</b> — %d из %d' % (
            len(result['entities']), result['total_entities']))
        print('telegram:', ok)
        return 0 if ok else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
