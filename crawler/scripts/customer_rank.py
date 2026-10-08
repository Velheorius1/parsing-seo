#!/usr/bin/env python3
"""Топ заказчиков нашей продукции по журналу закупок (purchase_ledger).

Балл = траты на полиграфию и мерч за последние 12 мес ×2 + за месяцы 13–24
(решение Данияра 07.10.2026). Группировка — по ИНН заказчика; строки без ИНН
(карточка ebirja ещё не прочитана) в рейтинг не входят и показаны в охвате.
Отказ и расторжение — не покупка (purchase_ledger.PURCHASE_STATUSES).

Результат — data/private/customer_rank_<дата>.json и .html (репозиторий
публичный: список заказчиков в git не кладём). --tg шлёт HTML документом.

Реестр (фаза 3, core/customer_registry): подтверждённые группы ИНН (структуры
одной организации) складываются в одну строку; --registry пересобирает реестр
и кладёт месячный снимок. Клиенты Битрикса — из data/private/bitrix_companies.json
(выгрузка на Маке, customer_registry_edit bitrix-export).

  python3 -m crawler.scripts.customer_rank --top 100
  python3 -m crawler.scripts.customer_rank --top 100 --registry --tg
"""
import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from crawler.core import customer_registry as CR
from crawler.core import purchase_ledger as L
from crawler.core import purchase_profile as P
from crawler.scripts.purchase_backfill import TABLE

PRIVATE_DIR = Path(__file__).resolve().parents[2] / 'data' / 'private'
_COLS = ('id,feed,business_id,buyer_inn,buyer_name,buyer_type,winner_inn,winner_name,subject,'
         'amount_uzs,awarded_on,status,profile,source_url')
BITRIX_PATH = PRIVATE_DIR / 'bitrix_companies.json'
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


def rank(rows, today, top, groups=None):
    # type: (List[Dict[str, Any]], date, Optional[int], Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]
    """top None — весь рейтинг (для реестра). groups — ИНН → подтверждённая группа."""
    groups = groups or {}
    cut12 = (today - timedelta(days=365)).isoformat()
    buckets = defaultdict(list)  # type: Dict[str, List[Dict[str, Any]]]
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
        member = groups.get(row['buyer_inn'])
        buckets[member['id'] if member else row['buyer_inn']].append(row)
    entities = []
    for key, items in buckets.items():
        member = groups.get(items[0]['buyer_inn'])
        inns = list(member['inns']) if member else [key]
        spend12 = sum(Decimal(str(r['amount_uzs'])) for r in items if r['awarded_on'] >= cut12)
        spend24 = sum(Decimal(str(r['amount_uzs'])) for r in items if r['awarded_on'] < cut12)
        total = spend12 + spend24
        names = Counter(r.get('buyer_name') for r in items if r.get('buyer_name'))
        name = member['name'] if member else (names.most_common(1)[0][0] if names else key)
        types = Counter(r.get('buyer_type') for r in items if r.get('buyer_type'))
        winners = defaultdict(Decimal)  # type: Dict[str, Decimal]
        for r in items:
            winners[r.get('winner_name') or r.get('winner_inn') or '?'] += Decimal(str(r['amount_uzs']))
        biggest = max(items, key=lambda r: Decimal(str(r['amount_uzs'])))
        entities.append({
            'inn': inns[0], 'inns': inns, 'name': name,
            'segment': P.segment(name, types.most_common(1)[0][0] if types else None),
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
    return {'entities': entities if top is None else entities[:top], 'total_entities': len(entities),
            'skipped': dict(skipped)}


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


def load_bitrix(path=None):
    # type: (Optional[Path]) -> Optional[Dict[str, Any]]
    try:
        return json.loads(Path(path or BITRIX_PATH).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def bitrix_line(bitrix, today):
    # type: (Optional[Dict[str, Any]], date) -> str
    """Возраст выгрузки Битрикса: старая выгрузка = «не клиент» может быть неправдой."""
    if not bitrix or not bitrix.get('as_of'):
        return 'Битрикс: выгрузки нет — клиентов не отмечаем'
    age = (today - date.fromisoformat(str(bitrix['as_of'])[:10])).days
    line = 'Битрикс: выгрузка от %s (%d дн. назад, компаний %d)' % (
        str(bitrix['as_of'])[:10], age, len(bitrix.get('companies') or []))
    if age > CR.BITRIX_STALE_DAYS:
        line = '⚠️ %s — старше %d дней, клиенты могли поменяться' % (line, CR.BITRIX_STALE_DAYS)
    return line


def bitrix_cell(entity, registry, bitrix):
    # type: (Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> str
    """«клиент: N заказов» по подтверждённой связи; «возможно: …» — по предложению."""
    if entity is None:
        return ''
    companies = {str(c['id']): c for c in (bitrix or {}).get('companies') or []}
    links = entity.get('bitrix') or []
    if links:
        orders = sum((companies.get(str(link['id'])) or {}).get('orders') or 0 for link in links)
        last = max([(companies.get(str(link['id'])) or {}).get('last_order') or '' for link in links])
        if not orders:
            # UzAuto 07.10: одна заявка в 2024, заказов не было — это не «клиент».
            requests = sum((companies.get(str(link['id'])) or {}).get('requests') or 0 for link in links)
            return 'в Битриксе: заявок %d, заказов нет' % requests
        return 'клиент: %d заказ(ов)%s' % (orders, ', посл. %s' % last[:7] if last else '')
    maybe = [p['bitrix_name'] for p in (registry or {}).get('bitrix_proposals') or []
             if p.get('status') == 'proposed' and p.get('inn') in entity.get('inns', [])]
    return 'возможно: %s' % ', '.join(maybe[:2]) if maybe else ''


def render_html(result, cov, today, metrics=None, registry=None, bitrix=None):
    # type: (Dict[str, Any], List[Dict[str, Any]], date, Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> str
    esc = html.escape
    registry = registry or CR.empty()
    reg = CR.by_inn(registry)
    rows, shown = [], set()  # type: (List[str], set)
    for i, e in enumerate(result['entities'], 1):
        entity = reg.get(e['inn'])
        shown.update(e.get('inns') or [e['inn']])
        star = '⭐ ' if entity and entity.get('pinned') else ''
        group = ' · ИНН в группе: %d' % len(e['inns']) if len(e.get('inns') or []) > 1 else ''
        winners = '<br>'.join('%s — %s' % (esc(w[:40]), _mln(s)) for w, s in e['winners'])
        rows.append(
            '<tr><td class="n">%d</td><td><b>%s%s</b><div class="m">ИНН %s · %s%s</div>'
            '<div class="m">%s</div></td><td class="n">%s</td><td class="n">%s</td><td class="n">%d'
            '<div class="m">полигр. %d · мерч %d</div></td><td class="n">%d%%<div class="m">%s</div></td>'
            '<td class="w">%s</td><td class="w">%s</td><td class="n">%s</td></tr>' % (
                i, star, esc(e['name']), e['inn'], esc((entity or {}).get('segment_override') or e['segment']),
                group, esc(' · '.join(e['examples'][:2])), _mln(e['spend12']), _mln(e['spend24']), e['purchases'],
                e['poly'], e['merch'], round(e['biggest_share'] * 100), esc(e['biggest_subject'][:70]),
                winners, esc(bitrix_cell(entity, registry, bitrix)), e['last']))
    pinned = [x for x in registry['entities'] if x.get('pinned') and not set(x['inns']) & shown]
    pinned_html = ''.join('<li>⭐ %s — ИНН %s · %s</li>' % (
        esc(x['name']), ', '.join(x['inns']),
        'место %s' % x['rank'] if x.get('rank') else 'покупок нашего профиля за 24 мес нет') for x in pinned)
    open_merge = [p for p in registry.get('merge_proposals') or [] if p.get('status') == 'proposed']
    open_bitrix = [p for p in registry.get('bitrix_proposals') or [] if p.get('status') == 'proposed']
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
<p>%s · ⭐ — закреплён · предложений ждут «да»: объединить %d, связать с Битриксом %d</p>
<div class="wrap"><table><tr><th>№</th><th>Заказчик</th><th class="n">12 мес</th><th class="n">13–24 мес</th>
<th class="n">Покупок</th><th class="n">Крупнейшая</th><th>Кто выигрывал</th><th>Битрикс</th><th class="n">Последняя</th></tr>
%s</table></div>
%s
<h2>Охват лент</h2><p>Не учтено: %s</p>
<div class="wrap"><table style="min-width:520px"><tr><th>Лента</th><th class="n">Договоров</th><th class="n">С даты</th>
<th class="n">Без ИНН</th><th class="n">Без оценки</th></tr>%s</table></div>
</body></html>""" % (len(result['entities']), today.isoformat(), result['total_entities'],
                     esc(status_line(cov, metrics if metrics is not None else judge_metrics())),
                     esc(bitrix_line(bitrix, today)), len(open_merge), len(open_bitrix), ''.join(rows),
                     '<h2>Закреплённые вне топа</h2><ul>%s</ul>' % pinned_html if pinned_html else '',
                     skipped, cov_rows)


def rebuild_registry(client, registry, today, top=100, months=24):
    # type: (Any, Dict[str, Any], date, int, int) -> Tuple[Dict[str, Any], Dict[str, Any]]
    """Пересобрать рейтинг и реестр (ручные поля и закреплённые переживают), сохранить
    реестр и месячный снимок. -> (реестр, полный рейтинг). Зовут --registry и недельный отчёт."""
    since = (today - timedelta(days=round(months * 30.44))).isoformat()
    full = rank(profile_rows(client, since), today, None, CR.groups(registry))
    stamp = today.isoformat()
    registry = CR.build(full['entities'], registry, stamp, top)
    CR.save(registry)
    CR.save(registry, PRIVATE_DIR / ('customer_registry_%s.json' % stamp[:7]))
    return registry, full


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
    parser.add_argument('--registry', action='store_true',
                        help='пересобрать реестр (data/private/customer_registry.json) и месячный снимок')
    args = parser.parse_args()
    today = date.today()
    since = (today - timedelta(days=round(args.months * 30.44))).isoformat()
    client = _client()
    registry = CR.load()
    full = rank(profile_rows(client, since), today, None, CR.groups(registry))
    result = dict(full, entities=full['entities'][:args.top])
    cov = coverage(client)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = today.isoformat()
    if args.registry:
        registry = CR.build(full['entities'], registry, stamp, args.top)
        CR.save(registry)
        CR.save(registry, PRIVATE_DIR / ('customer_registry_%s.json' % stamp[:7]))
    json_path = PRIVATE_DIR / ('customer_rank_%s.json' % stamp)
    html_path = PRIVATE_DIR / ('customer_rank_%s.html' % stamp)
    json_path.write_text(json.dumps({'generated_at': datetime.now(timezone.utc).isoformat(), 'since': since,
                                     'coverage': cov, **result}, ensure_ascii=False, indent=1), encoding='utf-8')
    html_path.write_text(render_html(result, cov, today, registry=registry, bitrix=load_bitrix()), encoding='utf-8')
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
