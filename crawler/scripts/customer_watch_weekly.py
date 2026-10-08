"""Недельный отчёт топ-100: знали ли мы, что топ-100 покупал наше, и учится ли система (фаза 7).

Понедельник 06:15 UTC (крон, ops/install-customer-watch-cron.sh). По шагам:
  1. новый месяц против as_of реестра — пересобрать рейтинг и реестр, затем индекс ⭐
     (план: «пересборка раз в месяц»; ручные поля и закреплённые переживают);
  2. детектор пропусков (customer_miss.run): вид каждой покупки, полнота, строка истории;
  3. предложения правок (propose_fixes.run): новые уходят после отчёта своими сообщениями с кнопками;
  4. Telegram: полнота и её тренд, новые пропуски с видом и причиной, ⭐-алерты недели
     и отметки Данияра на них (от них зависит перевод полосы в пуш), предложения, охват
     каждой ленты без «тихих нулей», карточки в очереди, возраст выгрузки Битрикса;
  5. HTML-вложение (protect_content): каждый заказчик топ-100 и его покупки — узнали / не узнали.

Чат — личный чат алертов Данияра; отдельный чат не нужен. Всё про заказчиков —
только data/private (репозиторий публичный).

  python3 -m crawler.scripts.customer_watch_weekly --send
  python3 -m crawler.scripts.customer_watch_weekly --dry-run --no-ai     # без записи и отправки
"""
import argparse
import html
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from crawler.core import customer_registry as CR
from crawler.core import vip_lane as V
from crawler.scripts.customer_rank import FEED_RU, PRIVATE_DIR

STATE_PATH = PRIVATE_DIR / 'customer_watch_state.json'
HISTORY_PATH = PRIVATE_DIR / 'customer_recall_history.jsonl'
TEXT_LIMIT = 3900
NEW_MISSES_SHOWN = 8
UNJUDGED_SHOWN = 100   # меньше — шум (08.10: 2 из 163 тыс.)
KNOWN_RU = {'WON': 'выиграли', 'KNOWN_LOST': 'знали — выиграл другой', 'KNOWN_REJECTED': 'знали — отклонили'}
MISS_RU = {'A_filter': 'отсёк фильтр', 'A_not_collected': 'лот не собрали', 'A_late': 'алерт опоздал',
           'B_no_announcement': 'без объявления', 'C_uncrawled_platform': 'площадку не собираем',
           'PRE_COVERAGE': 'до нашего сбора'}
STAGE_RU = {'no_keyword': 'нет ключевого слова', 'passes_today': 'сегодня уже проходит',
            'min_price': 'ниже порога цены', 'deadline_expired': 'срок истёк к сбору',
            'message_type': 'не тендер по типу', 'no_push_source': 'площадка выключена',
            'delivery_loss': 'AI взял, но не доставлено', 'reject_title': 'отсечён по названию',
            'undecided': 'AI не ответил', 'ai': 'отсеял AI', 'prefilter': 'отсёк фильтр'}
LABEL_RU = {'client': 'интересно', 'ad': 'реклама', 'irrelevant': 'не моё'}


# ── чистые функции ───────────────────────────────────────────────────────────

def _d(iso):
    # type: (Any) -> str
    """2026-10-08 → 08.10."""
    s = str(iso or '')
    return '%s.%s' % (s[8:10], s[5:7]) if len(s) >= 10 else s


def purchases(n):
    # type: (int) -> str
    if n % 10 == 1 and n % 100 != 11:
        return 'покупка'
    return 'покупки' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'покупок'


def pct(value):
    # type: (Optional[float]) -> str
    return '—' if value is None else '%d%%' % round(value * 100)


def money(value):
    # type: (Any) -> str
    v = float(value or 0)
    return ('%.1f млрд' % (v / 1e9)).replace('.', ',') if v >= 1e9 else '%d млн' % round(v / 1e6)


def previous(history, now, min_days=6):
    # type: (Iterable[Dict[str, Any]], datetime, int) -> Optional[Dict[str, Any]]
    """Последняя строка истории не моложе min_days — с ней сравниваем неделю."""
    edge = (now - timedelta(days=min_days)).isoformat()
    older = [h for h in history if str(h.get('generated_at') or '') <= edge]
    return older[-1] if older else None


def delta(current, before):
    # type: (Optional[float], Optional[float]) -> str
    if current is None or before is None:
        return ''
    diff = round((current - before) * 100)
    return ' (%s%d п.п. за неделю)' % ('+' if diff >= 0 else '−', abs(diff)) if diff else ' (без изменений)'


def new_misses(rows, since, seen=None):
    # type: (List[Dict[str, Any]], str, Optional[set]) -> List[Dict[str, Any]]
    """Пропуски, которых не было в прошлом разборе, — самые дорогие первыми.

    seen — ключи (лента, id) пропусков прошлого разбора. По дате договора судить
    нельзя: журнал догружает договоры задним числом (карточки ebirja, детали
    прямых закупок), и пропуск с договором двухнедельной давности, впервые
    увиденный на этой неделе, — новый. Прошлого разбора нет — по дате since."""
    out = [r for r in rows if r.get('miss_type') in MISS_RU and r['miss_type'] != 'PRE_COVERAGE'
           and ((r['feed'], str(r['business_id'])) not in seen if seen is not None
                else str(r.get('awarded_on') or '') >= since)]
    return sorted(out, key=lambda r: -float(r.get('amount_uzs') or 0))


def seen_misses(today):
    # type: (date) -> Optional[set]
    """Ключи пропусков из последнего разбора детектора раньше сегодняшнего."""
    files = sorted(p for p in PRIVATE_DIR.glob('customer_miss_*.json')
                   if p.stem[len('customer_miss_'):] < today.isoformat())
    if not files:
        return None
    detail = read_json(files[-1], {}).get('purchases_detail') or []
    return set((r['feed'], str(r['business_id'])) for r in detail if r.get('miss_type') in MISS_RU)


def feed_health(counts):
    # type: (Dict[str, Dict[str, float]]) -> List[str]
    """«лента: за неделю / обычно». Ноль там, где обычно не ноль, и провал втрое — ⚠️:
    тихий ноль ленты читается как «пропусков нет», а это сломанный сбор."""
    out = []
    for feed, c in counts.items():
        week, usual = int(c.get('week') or 0), float(c.get('usual') or 0)
        warn = usual >= 1 and (week == 0 or week < usual / 2.0)
        out.append('%s%s %d / %s' % ('⚠️ ' if warn else '', FEED_RU.get(feed, feed), week,
                                     ('%.0f' % usual) if usual >= 1 else '0'))
    return out


def star_stats(alerts, index, keyword_hit, labels):
    # type: (List[Dict[str, Any]], Optional[Dict[str, Any]], Callable[[Dict[str, Any]], bool], Dict[int, str]) -> Dict[str, Any]
    """⭐-алерты недели: сколько у топ-100, сколько только по ⭐ и как их отметил Данияр."""
    starred = [a for a in alerts if index and V.match(_tender(a), index)]
    vip_only = [a for a in starred if not keyword_hit(a)]
    marks = Counter(labels.get(int(a['alert_seq'])) for a in vip_only)
    return {'alerts': len(alerts), 'starred': len(starred),
            'starred_push': sum(1 for a in starred if a.get('telegram_message_id')),
            'vip_only': len(vip_only), 'vip_only_push': sum(1 for a in vip_only if a.get('telegram_message_id')),
            'marked': dict((LABEL_RU.get(k, k), v) for k, v in marks.items() if k),
            'unmarked': marks.get(None, 0)}


def _tender(row):
    # type: (Dict[str, Any]) -> Any
    from crawler.core.tender_rows import row_to_raw_tender
    return row_to_raw_tender(row)


def render_text(ctx):
    # type: (Dict[str, Any]) -> str
    """Текст для Telegram (HTML-режим; всё чужое экранировано), не длиннее TEXT_LIMIT."""
    e = html.escape
    s, prev = ctx['summary'], ctx.get('previous') or {}
    types = s.get('types') or {}
    known, missed = s.get('known') or 0, s.get('missed') or 0
    b = types.get('B_no_announcement') or 0
    lines = ['📊 <b>Топ-100: неделя %s–%s</b>' % (_d(ctx['week_from']), _d(ctx['week_to'])), '']
    lines.append('Полнота по объявленным: знали %d из %d (%s), по деньгам %s%s.' % (
        known, known + missed - b, pct(s.get('recall_announced')), pct(s.get('recall_announced_uzs')),
        delta(s.get('recall_announced'), prev.get('recall_announced'))))
    lines.append('Без объявления (прямые договоры, э-магазин): %d %s — словом не ловится.' % (b, purchases(b)))
    lines.append('Окно: покупки нашего профиля от 20 млн с %s; выиграли сами — %d.' % (
        _d(s.get('since')), types.get('WON') or 0))
    lines.append('')
    fresh = ctx['new_misses']
    if fresh:
        lines.append('<b>Новые пропуски</b> (нет в прошлом разборе): %d на %s' % (
            len(fresh), money(sum(float(r.get('amount_uzs') or 0) for r in fresh))))
        for r in fresh[:NEW_MISSES_SHOWN]:
            why = MISS_RU.get(r['miss_type'], r['miss_type'])
            if r.get('root_stage'):
                why += ': ' + STAGE_RU.get(r['root_stage'], r['root_stage'])
            lines.append('• %s — «%s», %s — %s' % (e(_short(r.get('buyer_name'), 32)), e(_short(r.get('subject'), 60)),
                                                money(r.get('amount_uzs')), e(why)))
        if len(fresh) > NEW_MISSES_SHOWN:
            lines.append('…и ещё %d — во вложении' % (len(fresh) - NEW_MISSES_SHOWN))
    else:
        lines.append('<b>Новых пропусков</b> за неделю нет.')
    lines.append('')
    st = ctx['stars']
    lines.append('<b>⭐ за неделю</b> (режим полосы: %s): алертов у топ-100 %d из %d, только по ⭐ — %d '
                 '(в пуш %d).' % (e(ctx['vip_mode']), st['starred'], st['alerts'], st['vip_only'], st['vip_only_push']))
    if st['vip_only']:
        marks = ', '.join('%s %d' % (k, v) for k, v in sorted(st['marked'].items())) or 'отметок нет'
        lines.append('Твои отметки на «только по ⭐»: %s; без отметки %d.' % (marks, st['unmarked']))
    lines.append('')
    pf = ctx['proposals']
    lines.append('<b>Предложения</b>: новых %d%s; ждут решения %d; одобрено и ждёт коммита: %s.' % (
        pf.get('new_or_unsent') or 0, ' — ниже отдельным сообщением' if pf.get('new_or_unsent') else '',
        ctx['pending_decisions'], e(', '.join(pf.get('waiting_commit') or []) or 'нет')))
    lines.append('')
    lines.append('<b>Охват лент</b> (договоров за неделю по %s / обычно): ' % _d(ctx['feeds_to'])
                 + ' · '.join(e(x) for x in ctx['feeds']))
    if (ctx.get('unjudged') or 0) >= UNJUDGED_SHOWN:
        lines.append('Договоров без оценки предмета: %d — пока их не разберут, полнота неточна.' % ctx['unjudged'])
    lines.append(e(ctx['bitrix_line']))
    if ctx.get('rebuilt'):
        lines.append('Реестр топ-100 пересобран за %s (раз в месяц).' % e(ctx['rebuilt']))
    text = '\n'.join(lines)
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT - 1] + '…'


def _short(text, limit):
    # type: (Any, int) -> str
    s = ' '.join(str(text or '').split())
    return s if len(s) <= limit else s[:limit - 1] + '…'


def render_html(registry, rows, per_customer, ctx):
    # type: (Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]) -> str
    """Каждый заказчик топ-100 и его покупки: узнали / не узнали."""
    e = html.escape
    by_inn = CR.by_inn(registry)
    recall_by_id = dict((c['id'], c) for c in per_customer)
    purchases = {}  # type: Dict[str, List[Dict[str, Any]]]
    for r in rows:
        entity = by_inn.get(r.get('buyer_inn'))
        if entity:
            purchases.setdefault(entity['id'], []).append(r)
    blocks = []
    for entity in sorted(registry.get('entities') or [], key=lambda x: x.get('rank') or 999):
        mine = sorted(purchases.get(entity['id'], []), key=lambda r: str(r.get('awarded_on')), reverse=True)
        rc = recall_by_id.get(entity['id']) or {}
        head = '<h3>%s. %s <span class="m">%s · полнота %s, по деньгам %s</span></h3>' % (
            entity.get('rank') or '—', e(entity['name']), e(entity.get('segment') or ''),
            pct(rc.get('recall')), pct(rc.get('recall_uzs')))
        if not mine:
            blocks.append(head + '<p class="m">Покупок нашего профиля от 20 млн с %s нет.</p>' % e(ctx['since']))
            continue
        trs = []
        for r in mine:
            kind = r.get('miss_type') or ''
            cls = 'k' if kind in KNOWN_RU else ('x' if kind == 'PRE_COVERAGE' else 'mi')
            label = KNOWN_RU.get(kind) or MISS_RU.get(kind, kind)
            if r.get('root_stage'):
                label += ': ' + STAGE_RU.get(r['root_stage'], r['root_stage'])
            link = ('<a href="%s">%s</a>' % (e(r['source_url']), e(_short(r.get('subject'), 90)))
                    if r.get('source_url') else e(_short(r.get('subject'), 90)))
            trs.append('<tr><td class="n">%s</td><td>%s</td><td class="n">%s</td><td>%s</td>'
                       '<td><span class="b %s">%s</span></td></tr>' % (
                           e(str(r.get('awarded_on') or '')), link, money(r.get('amount_uzs')),
                           e(_short(r.get('winner_name'), 40)), cls, e(label)))
        blocks.append(head + '<div class="wrap"><table><tr><th class="n">Договор</th><th>Предмет</th>'
                      '<th class="n">Сумма</th><th>Победитель</th><th>Мы</th></tr>%s</table></div>' % ''.join(trs))
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Топ-100: неделя</title>
<style>
:root{--bg:#fbfaf7;--fg:#1c1b19;--muted:#6b6862;--line:#e4e1da;--head:#f1eee8;--ok:#2f7d4f;--okb:#e3f1e8;--bad:#a8401f;--badb:#f8e6df}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe7;--muted:#9c9a94;--line:#2c2b28;--head:#1f1e1b;--ok:#7fcf9c;--okb:#1d2e23;--bad:#f0a084;--badb:#3a221a}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:16px;max-width:1100px}
h1{font-size:20px;margin:0 0 4px}h3{font-size:15px;margin:22px 0 6px}p{margin:4px 0 12px}.m{color:var(--muted);font-weight:400;font-size:12px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%%;min-width:720px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left;font-size:13px}
th{background:var(--head);font-weight:600;font-size:12px}.n{text-align:right;white-space:nowrap}
a{color:inherit}.b{display:inline-block;border-radius:4px;padding:1px 6px;font-size:12px;white-space:nowrap}
.k{background:var(--okb);color:var(--ok)}.mi{background:var(--badb);color:var(--bad)}.x{color:var(--muted)}
</style></head><body>
<h1>Топ-100: знали ли мы, что они покупали наше</h1>
<p class="m">Неделя %s–%s · покупки полиграфии и мерча от 20 млн с %s · «до нашего сбора» в полноту не входит · суммы — сум</p>
%s
</body></html>""" % (e(ctx['week_from']), e(ctx['week_to']), e(ctx['since']), ''.join(blocks))


# ── чтение ───────────────────────────────────────────────────────────────────

def _client():
    # type: () -> Any
    from crawler.core.db import _get_client
    return _get_client()


def read_json(path, default):
    # type: (Any, Any) -> Any
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def read_history():
    # type: () -> List[Dict[str, Any]]
    try:
        with open(str(HISTORY_PATH), encoding='utf-8') as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        return []


FEED_LAG_DAYS = 3


def feed_counts(client, today):
    # type: (Any, date) -> Dict[str, Dict[str, float]]
    """Договоров за неделю и в среднем за 4 недели до неё — по каждой ленте.

    Неделя кончается FEED_LAG_DAYS дней назад: договоры последних дней лента
    публикует и журнал догружает с опозданием, и «свежая» неделя всегда ниже
    обычной (08.10: прямые закупки 162 против 438) — тревога была бы ложной."""
    end = today - timedelta(days=FEED_LAG_DAYS)
    week_from = (end - timedelta(days=7)).isoformat()
    base_from = (end - timedelta(days=35)).isoformat()
    out = {}
    for feed in FEED_RU:
        def count(lo, hi):
            return client.table('purchase_ledger').select('id', count='exact').eq('feed', feed) \
                .gte('awarded_on', lo).lt('awarded_on', hi).limit(1).execute().count or 0
        out[feed] = {'week': count(week_from, end.isoformat()), 'usual': count(base_from, week_from) / 4.0}
    return out


def unjudged(client):
    # type: (Any) -> int
    """Строки журнала без оценки предмета (ждут карточку или AI). Строка без карточки,
    уже оценённая по разделу прямой закупки, сюда не входит: 08.10 «75 тыс. в очереди»
    оказались прямыми закупками чужих разделов, которым карточка не нужна."""
    return client.table('purchase_ledger').select('id', count='exact').is_('profile', 'null') \
        .is_('deleted_at', 'null').limit(1).execute().count or 0


def week_alerts(client, last_seq, since):
    # type: (Any, Optional[int], str) -> List[Dict[str, Any]]
    """Алерты недели. Время отправки в базе не хранится — только номер: берём номера
    после прошлого отчёта, а в первый раз — лоты, появившиеся за 7 дней."""
    cols = 'external_id,source,title,organization,search_text,extra_info,alert_seq,telegram_message_id'
    rows, last = [], 0  # type: List[Dict[str, Any]], int
    while True:
        q = client.table('tenders').select(cols).not_.is_('alert_seq', 'null')
        q = q.gt('alert_seq', max(last, last_seq)) if last_seq else q.gte('created_at', since).gt('alert_seq', last)
        page = q.order('alert_seq').limit(1000).execute().data or []
        rows.extend(page)
        if len(page) < 1000:
            return rows
        last = page[-1]['alert_seq']


def pending_decisions(client):
    # type: (Any) -> int
    return client.table('learning_proposals').select('id', count='exact').eq('status', 'proposed') \
        .limit(1).execute().count or 0


# ── сборка ───────────────────────────────────────────────────────────────────

def run(args):
    # type: (Any) -> int
    from crawler.scripts import customer_miss as CM
    from crawler.scripts import customer_rank as RK
    from crawler.scripts import propose_fixes as PF
    from crawler.scripts.competitor_wins_weekly import fetch_human_labels
    client, today, now = _client(), date.today(), datetime.now(timezone.utc)
    state = read_json(STATE_PATH, {})
    registry = CR.load()
    rebuilt = None
    if not args.dry_run and str(registry.get('as_of') or '')[:7] != today.isoformat()[:7]:
        from crawler.scripts import vip_lane as VS
        registry, _full = RK.rebuild_registry(client, registry, today)
        VS.write_index(VS.build(client, registry))
        rebuilt = today.strftime('%m.%Y')
    since = str(CM.coverage(client)['deals'] or (today - timedelta(days=365)))
    summary, rows, per_customer = CM.run(client, registry, since, today, not args.no_ai, args.ai_calls, args.dry_run)
    hit, current = PF._matcher()
    proposals = {'new_or_unsent': 0, 'waiting_commit': [], 'fresh': []}  # type: Dict[str, Any]
    if not args.no_ai:
        proposals = PF.run(client, hit, current, dry_run=args.dry_run, send_tg=False)
    week_from = state.get('last_run') or (today - timedelta(days=7)).isoformat()
    index = V.load_index()
    alerts = week_alerts(client, state.get('last_seq'), (now - timedelta(days=7)).isoformat())
    labels = fetch_human_labels(client, [int(a['alert_seq']) for a in alerts]) if alerts else {}
    ctx = {
        'week_from': week_from, 'week_to': today.isoformat(), 'since': since, 'summary': summary,
        'previous': previous(read_history(), now),
        'new_misses': new_misses(rows, week_from, seen_misses(today)), 'vip_mode': V.mode(),
        'stars': star_stats(alerts, index, lambda a: hit(a, current), labels),
        'proposals': proposals, 'pending_decisions': pending_decisions(client),
        'feeds': feed_health(feed_counts(client, today)),
        'feeds_to': (today - timedelta(days=FEED_LAG_DAYS + 1)).isoformat(), 'unjudged': unjudged(client),
        'bitrix_line': RK.bitrix_line(RK.load_bitrix(), today), 'rebuilt': rebuilt,
    }
    text = render_text(ctx)
    page = render_html(registry, rows, per_customer, ctx)
    path = PRIVATE_DIR / ('customer_watch_%s.html' % today.isoformat())
    if args.dry_run:
        print(text)
        print(json.dumps({'html_bytes': len(page), 'alerts': len(alerts), 'stars': ctx['stars']}, ensure_ascii=False))
        return 0
    path.write_text(page, encoding='utf-8')
    ok = True
    if args.send:
        ok = _send_text(text) and RK.send_document(path, '<b>Топ-100 по заказчикам</b> — покупки и «узнали / не узнали»')
        if proposals.get('fresh'):
            PF.send(client, proposals['fresh'])
    STATE_PATH.write_text(json.dumps({'last_run': today.isoformat(), 'last_seq': max(
        [int(a['alert_seq']) for a in alerts] + [int(state.get('last_seq') or 0)]), 'sent': ok},
        ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'sent': ok, 'html': str(path), 'chars': len(text), 'stars': ctx['stars'],
                      'new_misses': len(ctx['new_misses'])}, ensure_ascii=False))
    return 0 if ok else 1


def _send_text(text):
    # type: (str) -> bool
    import httpx
    from crawler.config.settings import settings
    r = httpx.post('https://api.telegram.org/bot%s/sendMessage' % settings.telegram_bot_token,
                   json={'chat_id': settings.telegram_alert_chat_id, 'text': text, 'parse_mode': 'HTML',
                         'disable_web_page_preview': True, 'protect_content': True}, timeout=20)
    ok = r.status_code == 200 and bool(r.json().get('ok'))
    if not ok:
        print('telegram text failed: %s' % r.text[:200])
    return ok


def main(argv=None):
    # type: (Any) -> int
    parser = argparse.ArgumentParser(description='Недельный отчёт топ-100 (фаза 7)')
    parser.add_argument('--send', action='store_true', help='прислать текст, HTML и новые предложения')
    parser.add_argument('--dry-run', action='store_true', help='без записи, без отправки, без пересборки')
    parser.add_argument('--no-ai', action='store_true', help='детектор без AI, предложения не считать')
    parser.add_argument('--ai-calls', type=int, default=80)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        # Отчёт, который молча не пришёл, читается как «всё спокойно» — сбой кричит.
        if args.send:
            from crawler.scripts.fetch_ebirja_contracts import _send_telegram_alert
            _send_telegram_alert('⚠️ Недельный отчёт топ-100 упал: %s' % html.escape(str(exc)[:300]))
        raise


if __name__ == '__main__':
    sys.exit(main())
