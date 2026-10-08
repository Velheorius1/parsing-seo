"""Предложения правок фильтра с бэктестом: система предлагает, Данияр жмёт «да» (фаза 5).

Вход — разбор детектора пропусков (customer_miss, фаза 4) в purchase_ledger:
покупки топ-100 нашего профиля от 20 млн, которые прошли мимо нас.

Слово (A_filter / no_keyword: лот лежал у нас в базе и умер на ключевых словах).
Кандидатов предлагает AI по текстам этих лотов — без списков-рельс, — а проверяет
код, тем же сопоставителем, что у гейта:
  • пропуски: сколько пропущенных лотов слово поймало бы и сколько из них дошло
    бы до Данияра (replay с AI, как у детектора). На этих лотах слово и придумано,
    поэтому цифра заведомо оптимистичная — отсюда второй замер;
  • свежие 14 дней, вне выборки: сколько НОВЫХ лотов слово добавит к фильтру
    (все строки окна, без потолка на ленту), сколько пройдёт остальные стадии и
    какую долю возьмёт AI → ≈ алертов в неделю, с примерами.
Слово, которое не довело бы до Данияра ни одного пропуска, не предлагается.
Включается слово только коммитом в alert_keywords с тестом (правило «слова только
коммитом», shadow_search.promote); «да» — разрешение на такой коммит. --sync
ставит одобренному слову applied, когда оно появилось в живом словаре.

Площадка (B — покупка без объявления, C — площадка, которую не собираем).
Словом не лечится: нужен каталог в э-магазине, продажи через заказчика или новый
источник. Предложение — сколько денег топ-100 прошло там мимо нас и у кого.

Хранение — learning_proposals (миграция 025): отклонённое второй раз не
предлагается. Кнопки «да / нет» ловит feedback_bot (core/learning).

  python3 -m crawler.scripts.propose_fixes                     # предложить и отправить
  python3 -m crawler.scripts.propose_fixes --dry-run           # только посчитать
  python3 -m crawler.scripts.propose_fixes --words нашр,matbaa --dry-run
  python3 -m crawler.scripts.propose_fixes --sync              # одобренное → applied
"""
import argparse
import asyncio
import html
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from crawler.core import competitor_wins as CW
from crawler.core import learning as L

WINDOW_DAYS = 14
WINDOW_PAGE = 1000          # потолок PostgREST на ответ
WINDOW_MAX_ROWS = 20000     # сверх — окно усечено, и предложение говорит это вслух
JUDGE_PER_WORD = 10         # AI-проверок на слово в окне
AI_CAP = 150                # AI-вызовов replay за прогон (пропуски + окна)
MAX_WORDS = 10
NOISY_WEEK = 10             # ≈ алертов в неделю, после которых слово помечается шумным
GEN_MODEL = 'deepseek/deepseek-v4-pro'   # раз в прогон, один вызов: модель не экономим
LEDGER_COLS = ('feed,business_id,buyer_inn,buyer_name,winner_name,subject,amount_uzs,awarded_on,'
               'lot_key,source_url,miss_type,root_stage')
CHANNEL_TYPES = ('B_no_announcement', 'C_uncrawled_platform')
CHANNEL_TEXT = {
    'ebirja_shop': ('Э-магазин ebirja', 'объявления нет: заказчик выбирает из каталога. '
                    'Выход — свои позиции в каталоге э-магазина'),
    'direct': ('Прямые договоры UZEX', 'объявления нет: договор с одним поставщиком. '
               'Выход — продажи через самого заказчика'),
    'ebirja_selection': ('Отборы ebirja', 'объявления отборов ebirja мы не собираем. '
                         'Выход — подключить их как источник'),
    'ebirja_auction': ('Аукционы ebirja', 'аукционы ebirja не связываются с нашими лотами. '
                       'Выход — подключить объявления как источник'),
    'ebirja_tender': ('Тендеры ebirja', 'объявления тендеров ebirja мы не собираем. '
                      'Выход — подключить их как источник'),
}
_WORD_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|['ʻʼ‘’ -])*$", re.U)


# ── чистые функции ───────────────────────────────────────────────────────────

def _matcher():
    # type: () -> Tuple[Callable[[Dict[str, Any], List[str]], bool], List[str]]
    """(совпадает ли строка лота со словами — тем же сопоставителем, что у гейта; живой словарь)."""
    from crawler.core.notifier import _find_matching_keyword, _get_keywords
    from crawler.core.tender_rows import row_to_raw_tender

    def hit(row, words):
        return _find_matching_keyword(row_to_raw_tender(row), words) is not None
    return hit, _get_keywords()


def normalize_word(word, current, hit):
    # type: (Any, List[str], Callable[[Dict[str, Any], List[str]], bool]) -> Optional[str]
    """Слово-кандидат в виде для словаря или None: мусор, уже в словаре, уже покрыто."""
    kw = ' '.join(str(word or '').lower().split())
    if not 3 <= len(kw) <= 30 or not _WORD_RE.match(kw) or ',' in kw:
        return None
    if kw in current or hit({'title': kw}, current):
        return None     # «kitobi» ловит уже стоящее «kitob» — предлагать нечего
    return kw


def build_prompt(lots, current):
    # type: (List[Dict[str, Any]], List[str]) -> str
    lines = []
    for i, item in enumerate(lots, 1):
        lot = item['lot']
        text = ' '.join(str(lot.get('search_text') or '').split())[:200]
        lines.append('%d. %s — %s' % (i, ' '.join(str(lot.get('title') or '').split())[:200], text))
    return (
        'Ты настраиваешь словарь ключевых слов фильтра тендеров для типографии Winch '
        '(полиграфия и сувенирная продукция с нанесением логотипа). Ниже лоты, которые были '
        'нашим профилем, но фильтр их не пропустил: ни одно слово словаря в них не нашлось.\n\n'
        'Как работает словарь: слово ищется в начале слова текста (как префикс), регистр не '
        'важен; русские слова длиннее 4 букв обрезаются до основы. Тексты — на русском, '
        'узбекском латиницей и узбекском кириллицей.\n\n'
        'Предложи до %d слов, которые поймали бы эти лоты. Слово должно буквально стоять в '
        'начале слова текста лота. Нужны слова, которые означают печать, издание или сувенирку, '
        'а не общие (услуга, поставка, 2026, названия организаций): каждое лишнее совпадение — '
        'лишний алерт. Одна точная основа лучше нескольких форм. Не повторяй слова словаря.\n\n'
        'Словарь сейчас: %s\n\n'
        'Ответ — только JSON: {"candidates": [{"keyword": "...", "lots": [номера], '
        '"why": "коротко по-русски"}]}\n\nЛоты:\n%s'
    ) % (MAX_WORDS, ', '.join(current), '\n'.join(lines))


def parse_candidates(text):
    # type: (str) -> List[Dict[str, str]]
    from crawler.core.notifier import _extract_json_object
    obj = _extract_json_object(text or '') or {}
    out = []
    for item in obj.get('candidates') or []:
        if isinstance(item, dict) and item.get('keyword'):
            out.append({'keyword': str(item['keyword']), 'why': str(item.get('why') or '')[:200]})
    return out


def estimate_week(passed, judged, accepted, days):
    # type: (int, int, int, int) -> Optional[float]
    """≈ новых алертов в неделю: прошло фильтр за окно × доля, которую взял AI."""
    if not judged or not days:
        return None
    return round(passed / float(days) * 7 * accepted / float(judged), 1)


def helps(verdicts):
    # type: (List[Any]) -> int
    """Сколько пойманных пропусков дошло бы до Данияра (без AI — прошедшие фильтр)."""
    return sum(1 for v in verdicts if v.delivered is True or (v.delivered is None and v.passed_prefilter))


def channel_gaps(rows):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Покупки B/C по лентам: сколько, на сколько, у кого и кто выиграл."""
    by_feed = defaultdict(list)  # type: Dict[str, List[Dict[str, Any]]]
    for r in rows:
        if r.get('miss_type') in CHANNEL_TYPES:
            by_feed[r['feed']].append(r)
    out = []
    for feed, items in by_feed.items():
        buyers, winners = Counter(), Counter()  # type: Counter, Counter
        for r in items:
            buyers[r.get('buyer_name') or r.get('buyer_inn') or '—'] += float(r.get('amount_uzs') or 0)
            winners[r.get('winner_name') or '—'] += float(r.get('amount_uzs') or 0)
        top = sorted(items, key=lambda r: -float(r.get('amount_uzs') or 0))
        out.append({'kind': 'channel', 'key': feed,
                    'payload': {'feed': feed, 'miss_type': items[0]['miss_type']},
                    'evidence': {'count': len(items), 'amount_uzs': sum(float(r.get('amount_uzs') or 0) for r in items),
                                 'buyers': buyers.most_common(5), 'winners': winners.most_common(5),
                                 'examples': [{k: r.get(k) for k in ('buyer_name', 'winner_name', 'subject',
                                                                       'amount_uzs', 'source_url')} for r in top[:3]]},
                    'backtest': {}})
    return sorted(out, key=lambda p: -p['evidence']['amount_uzs'])


def money(value):
    # type: (Any) -> str
    v = float(value or 0)
    if v >= 1e9:
        return ('%.1f млрд' % (v / 1e9)).replace('.', ',')
    return '%d млн' % round(v / 1e6)


def purchases(n):
    # type: (int) -> str
    if n % 10 == 1 and n % 100 != 11:
        return 'покупка'
    return 'покупки' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'покупок'


def _e(text, limit=90):
    # type: (Any, int) -> str
    s = ' '.join(str(text or '').split())
    return html.escape(s[:limit - 1] + '…' if len(s) > limit else s)


def line(p):
    # type: (Dict[str, Any]) -> str
    """Одна строка предложения для Telegram (HTML; всё чужое экранировано)."""
    ev, bt = p['evidence'], p['backtest']
    if p['kind'] != 'keyword':
        name, why = CHANNEL_TEXT.get(p['key'], (p['key'], ''))
        return ('<b>#%s %s</b> — %d %s на %s. %s. Больше всех купили: %s. Выиграли: %s.' % (
            p.get('id', '—'), html.escape(name), ev['count'], purchases(ev['count']), money(ev['amount_uzs']),
            html.escape(why[:1].upper() + why[1:]),
            ', '.join('%s %s' % (_e(n, 32), money(v)) for n, v in ev['buyers'][:3]),
            ', '.join('%s %s' % (_e(n, 32), money(v)) for n, v in ev['winners'][:3])))
    out = '<b>#%s «%s»</b> — пропусков ловит %d из %d (%s), %s %d.' % (
        p.get('id', '—'), html.escape(p['key']), ev['caught'], ev['missed_total'], money(ev['amount_uzs']),
        'до тебя дошло бы' if ev.get('ai_checked') else 'фильтр прошли бы (AI не проверял)', ev['delivered'])
    if bt.get('judged'):
        est = bt.get('est_alerts_week')
        out += ' За %d дней новых лотов через фильтр: %d, AI взял бы %d из %d → ≈ %s алерта в неделю%s.' % (
            bt['window_days'], bt['passed_prefilter'], bt['accepted'], bt['judged'],
            ('%s' % est).replace('.', ','), ' ⚠️ много' if (est or 0) > NOISY_WEEK else '')
    else:
        out += ' За %d дней новых лотов через фильтр: %d%s.' % (
            bt['window_days'], bt['passed_prefilter'],
            ', AI не проверял' if bt['passed_prefilter'] else ' — шума нет')
    if bt.get('truncated'):
        out += ' ⚠️ Окно усечено — шум оценён снизу.'
    if ev.get('lots'):
        # Пример — где слово стоит в самом предмете: «журнал» ловит и лот, где оно
        # только в описании, и такой пример читается как ошибка.
        top = next((l for l in ev['lots'] if p['key'] in str(l.get('subject') or '').lower()), ev['lots'][0])
        out += ' Напр.: «%s» (%s), %s.' % (_e(top.get('subject'), 60), _e(top.get('buyer_name'), 30),
                                          money(top.get('amount_uzs')))
    if bt.get('accepted_examples'):
        out += ' Новое пришло бы: «%s».' % _e(bt['accepted_examples'][0], 50)
    return out


HEADERS = {
    'keyword': ('💡 <b>Слова в фильтр алертов</b>\n'
                'Пропуски — покупки топ-100, чей лот был у нас и умер на словах (на них слово и придумано). '
                'Окно — свежие лоты вне этих пропусков: сколько добавится алертов.\n'
                '«Да» — добавлю слово в словарь коммитом с тестом.'),
    'channel': ('💡 <b>Где топ-100 покупает наше мимо объявлений</b>\n'
                'Словом это не лечится. «Да» — берём в работу.'),
}
MESSAGE_LIMIT = 3800


def messages(proposals):
    # type: (List[Dict[str, Any]]) -> List[Tuple[str, list, List[Any]]]
    """Предложения одного вида — одним сообщением, у каждого своя строка кнопок
    (десять сообщений подряд — простыня; кнопки дайджеста feedback_bot уже умеет
    гасить построчно). Длинное — режется на несколько сообщений."""
    out = []  # type: List[Tuple[str, list, List[Any]]]
    for kind in ('keyword', 'channel'):
        text, rows, ids = HEADERS[kind], [], []  # type: str, list, List[Any]
        for p in [p for p in proposals if p['kind'] == kind]:
            item = line(p)
            if ids and len(text) + len(item) + 2 > MESSAGE_LIMIT:
                out.append((text, rows, ids))
                text, rows, ids = HEADERS[kind], [], []
            text += '\n\n' + item
            label = p['key'] if kind == 'keyword' else CHANNEL_TEXT.get(p['key'], (p['key'], ''))[0]
            pid = int(p.get('id') or 0)     # 0 — сухой прогон, строки в базе ещё нет
            rows.append([{'text': '✅ %s' % label[:24], 'callback_data': L.callback_data(pid, 'ok')},
                         {'text': '❌ %s' % label[:24], 'callback_data': L.callback_data(pid, 'no')}])
            ids.append(p.get('id'))
        if ids:
            out.append((text, rows, ids))
    return out


# ── чтение ───────────────────────────────────────────────────────────────────

def _client():
    # type: () -> Any
    from crawler.core.db import _get_client
    return _get_client()


def ledger_misses(client):
    # type: (Any) -> List[Dict[str, Any]]
    rows = client.table('purchase_ledger').select(LEDGER_COLS) \
        .in_('miss_type', ['A_filter'] + list(CHANNEL_TYPES)).is_('deleted_at', 'null') \
        .limit(1000).execute().data or []
    if len(rows) >= 1000:
        raise RuntimeError('пропуски упёрлись в потолок 1000 строк — выборка неполна')
    return rows


def keyword_misses(client, rows):
    # type: (Any, List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Пропуски «умер на словах» вместе с нашей строкой лота (её и судит гейт)."""
    from crawler.scripts import competitor_wins_weekly as W
    rows = [r for r in rows if r.get('miss_type') == 'A_filter' and r.get('root_stage') == 'no_keyword']
    urls = sorted({r['source_url'] for r in rows if r['feed'] == 'deals' and r.get('source_url')})
    keys = sorted({r['lot_key'] for r in rows if r['feed'] == 'civil' and r.get('lot_key')})
    by_url = W.fetch_lot_rows(client, urls) if urls else {}
    by_key = W.fetch_civil_lot_rows(client, keys) if keys else {}
    out = []
    for r in rows:
        lots = by_key.get(CW.civil_norm_key(r.get('lot_key')), []) if r['feed'] == 'civil' \
            else by_url.get(r.get('source_url') or '', [])
        lot = CW.pick_lot_row(lots)
        if lot:
            out.append({'purchase': r, 'lot': lot})
    return out


def window_rows(client, word, since):
    # type: (Any, str, str) -> Tuple[List[Dict[str, Any]], bool]
    """Все лоты окна, где основа слова стоит где-нибудь в тексте (точно судит код).

    ilike по основе — заведомо шире сопоставителя гейта (тот требует начало слова),
    поэтому ничего не теряем; лишнее отсекает hit() у вызывающего.
    """
    from crawler.core.notifier import _MIN_STEM, _stem
    from crawler.scripts.competitor_wins_weekly import _DEAL_FIELDS
    stem = _stem(word) if len(word) > _MIN_STEM else word
    pattern = '"*%s*"' % stem.replace('"', '')
    rows = []  # type: List[Dict[str, Any]]
    for offset in range(0, WINDOW_MAX_ROWS, WINDOW_PAGE):
        page = client.table('tenders').select(_DEAL_FIELDS + ',alert_seq').gte('created_at', since) \
            .or_('title.ilike.%s,search_text.ilike.%s' % (pattern, pattern)) \
            .order('id').range(offset, offset + WINDOW_PAGE - 1).execute().data or []
        rows.extend(page)
        if len(page) < WINDOW_PAGE:
            return rows, False
    return rows, True


# ── суждение ─────────────────────────────────────────────────────────────────

async def _replay(rows, use_ai, keywords):
    # type: (List[Dict[str, Any]], bool, List[str]) -> List[Any]
    """Тот же replay, что у детектора (лот судится на дату первого появления),
    но со словарём «живой + кандидаты»."""
    if not rows:
        return []
    from crawler.core.tender_rows import row_to_raw_tender
    from crawler.scripts.replay import replay_tenders
    tenders = [row_to_raw_tender(r) for r in rows]
    first_seen = {t.external_id: r.get('created_at') for t, r in zip(tenders, rows)}
    return await replay_tenders(tenders, use_ai=use_ai, as_of='collected_at', keywords=keywords,
                                collected_at=first_seen)


async def _openrouter(prompt):
    # type: (str) -> str
    """Кандидаты от модели с рассуждением. 07.10 рассуждение съело весь лимит
    (4000 токенов) и вернуло пустой ответ — прогон тихо дал ноль слов. Поэтому
    лимит с запасом, а пустой ответ — повтор без рассуждения, не «кандидатов нет»."""
    import os
    import httpx
    from crawler.config.settings import settings
    model = os.getenv('PROPOSE_MODEL') or GEN_MODEL
    async with httpx.AsyncClient(timeout=300) as cl:
        for extra in ({'max_tokens': 20000}, {'max_tokens': 4000, 'reasoning': {'enabled': False}}):
            r = await cl.post('https://openrouter.ai/api/v1/chat/completions',
                              headers={'Authorization': 'Bearer %s' % settings.openrouter_api_key},
                              json=dict(extra, model=model, temperature=0.2,
                                        messages=[{'role': 'user', 'content': prompt}]))
            r.raise_for_status()
            content = (((r.json().get('choices') or [{}])[0]).get('message') or {}).get('content') or ''
            if content.strip():
                return content
    return ''


async def propose_words(client, misses, current, hit, words=None, ask=None, use_ai=True,
                        ai_cap=AI_CAP, judge=JUDGE_PER_WORD, days=WINDOW_DAYS, now=None, dropped=None):
    # type: (Any, List[Dict[str, Any]], List[str], Callable, Optional[List[str]], Optional[Callable[[str], Awaitable[str]]], bool, int, int, int, Optional[datetime], Optional[Dict[str, str]]) -> List[Dict[str, Any]]
    """Предложения-слова с бэктестом. dropped — куда записать, какой кандидат
    почему не прошёл: молча пустой список неотличим от сломанного AI."""
    dropped = {} if dropped is None else dropped
    if words is None:
        if not misses:
            return []
        raw = parse_candidates(await (ask or _openrouter)(build_prompt(misses, current)))
    else:
        raw = [{'keyword': w, 'why': ''} for w in words]
    cands, seen = [], set()  # type: List[Dict[str, str]], set
    if not raw:
        dropped['—'] = 'AI не дал ни одного кандидата'
    for c in raw:
        kw = normalize_word(c['keyword'], current, hit)
        if not kw:
            dropped[c['keyword']] = 'мусор или уже покрыто словарём'
        elif kw not in seen:
            seen.add(kw)
            cands.append({'keyword': kw, 'why': c['why']})
    cands = cands[:MAX_WORDS]
    caught = {c['keyword']: [m for m in misses if hit(m['lot'], [c['keyword']])] for c in cands}
    for c in cands:
        if not caught[c['keyword']]:
            dropped[c['keyword']] = 'не ловит ни одного пропуска'
    cands = [c for c in cands if caught[c['keyword']]]
    if not cands:
        return []
    budget = ai_cap
    lots = []  # type: List[Dict[str, Any]]
    for c in cands:
        for m in caught[c['keyword']]:
            if all(m is not x for x in lots):
                lots.append(m)
    with_ai = use_ai and budget >= len(lots)
    verdicts = await _replay([m['lot'] for m in lots], with_ai, current + [c['keyword'] for c in cands])
    budget -= len(lots) if with_ai else 0
    by_lot = {id(m): v for m, v in zip(lots, verdicts)}
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    missed_ids = {m['lot'].get('id') for m in misses}     # окно — вне выборки, где слово придумано
    out = []
    for c in cands:
        kw, mine = c['keyword'], caught[c['keyword']]
        mine_v = [by_lot[id(m)] for m in mine]
        if not helps(mine_v):
            dropped[kw] = 'пойманное (%d) всё равно отсеял бы фильтр или AI' % len(mine)
            continue
        rows, truncated = window_rows(client, kw, since)
        matched = [r for r in rows if hit(r, [kw])]
        new = [r for r in matched if r.get('alert_seq') is None and not hit(r, current)
               and r.get('id') not in missed_ids]
        pf = await _replay(new, False, current + [kw])
        passed = [r for r, v in zip(new, pf) if v.passed_prefilter]
        dropped = Counter(v.dropped_at_stage or 'prefilter' for v in pf if not v.passed_prefilter)
        n_judge = min(judge, len(passed), budget) if use_ai else 0
        sample = random.Random(kw).sample(passed, n_judge) if n_judge else []
        ai_v = await _replay(sample, True, current + [kw]) if sample else []
        budget -= len(sample)
        ok_ai = [(r, v) for r, v in zip(sample, ai_v) if not v.ai_error]
        accepted = [r for r, v in ok_ai if v.delivered]
        rejected = [r for r, v in ok_ai if not v.delivered]
        out.append({
            'kind': 'keyword', 'key': kw, 'payload': {'keyword': kw, 'why': c['why']},
            'evidence': {
                'missed_total': len(misses), 'caught': len(mine), 'delivered': helps(mine_v),
                'ai_checked': with_ai,
                'amount_uzs': sum(float(m['purchase'].get('amount_uzs') or 0) for m in mine),
                'lots': [dict({k: m['purchase'].get(k) for k in ('buyer_name', 'subject', 'amount_uzs', 'source_url')},
                              delivered=v.delivered)
                         for m, v in sorted(zip(mine, mine_v),
                                            key=lambda mv: -float(mv[0]['purchase'].get('amount_uzs') or 0))]},
            'backtest': {
                'window_days': days, 'since': since[:10], 'matched': len(matched), 'new': len(new),
                'passed_prefilter': len(passed), 'dropped': dict(dropped), 'judged': len(ok_ai),
                'accepted': len(accepted), 'ai_errors': len(sample) - len(ok_ai),
                'est_alerts_week': estimate_week(len(passed), len(ok_ai), len(accepted), days),
                'truncated': truncated,
                'accepted_examples': [r.get('title') for r in accepted[:3]],
                'rejected_examples': [r.get('title') for r in rejected[:3]]},
        })
    return sorted(out, key=lambda p: -p['evidence']['amount_uzs'])


# ── запись и отправка ───────────────────────────────────────────────────────

def save(client, proposals):
    # type: (Any, List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Новое — вставить; ждущее — освежить цифры; решённое — не трогать.
    -> строки, которые надо (пере)отправить: новые и ждущие без сообщения."""
    to_send = []
    now = datetime.now(timezone.utc).isoformat()
    for p in proposals:
        found = client.table(L.TABLE).select('id,status,telegram_message_id') \
            .eq('kind', p['kind']).eq('key', p['key']).limit(1).execute().data or []
        fields = {'payload': p['payload'], 'evidence': p['evidence'], 'backtest': p['backtest'], 'updated_at': now}
        if not found:
            row = (client.table(L.TABLE).insert(dict(fields, kind=p['kind'], key=p['key'])).execute().data or [{}])[0]
            to_send.append(dict(p, id=row.get('id')))
        elif found[0]['status'] == 'proposed':
            client.table(L.TABLE).update(fields).eq('id', found[0]['id']).execute()
            if not found[0].get('telegram_message_id'):
                to_send.append(dict(p, id=found[0]['id']))
    return to_send


def send(client, proposals):
    # type: (Any, List[Dict[str, Any]]) -> int
    """-> сколько предложений ушло; номер сообщения пишется в каждую строку."""
    import httpx
    from crawler.config.settings import settings
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return 0
    sent = 0
    for text, rows, ids in messages(proposals):
        r = httpx.post('https://api.telegram.org/bot%s/sendMessage' % settings.telegram_bot_token,
                       json={'chat_id': settings.telegram_alert_chat_id, 'text': text, 'parse_mode': 'HTML',
                             'disable_web_page_preview': True, 'reply_markup': {'inline_keyboard': rows}},
                       timeout=15)
        body = r.json() if r.status_code == 200 else {}
        message_id = (body.get('result') or {}).get('message_id')
        if not message_id:
            print('send %s failed: %s' % (ids, r.text[:200]))
            continue
        client.table(L.TABLE).update({'telegram_message_id': message_id}).in_('id', ids).execute()
        sent += len(ids)
    return sent


def sync(client, live):
    # type: (Any, List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]
    """Одобренные слова, появившиеся в живом словаре → applied. -> (включены, ждут коммита)."""
    rows = client.table(L.TABLE).select('id,kind,key,status,decided_at').eq('status', 'approved') \
        .execute().data or []
    applied, waiting = L.pending_keywords(rows, live)
    now = datetime.now(timezone.utc).isoformat()
    for row in applied:
        client.table(L.TABLE).update({'status': 'applied', 'applied_at': now, 'updated_at': now}) \
            .eq('id', row['id']).eq('status', 'approved').execute()
    return applied, waiting


def main(argv=None):
    # type: (Any) -> int
    parser = argparse.ArgumentParser(description='Предложения правок фильтра с бэктестом (фаза 5)')
    parser.add_argument('--dry-run', action='store_true', help='посчитать и напечатать, без записи и отправки')
    parser.add_argument('--no-send', action='store_true', help='записать, но не слать в Telegram')
    parser.add_argument('--words', help='проверить эти слова (через запятую) вместо кандидатов AI')
    parser.add_argument('--no-ai', action='store_true', help='без AI: только фильтр (кандидатов даёт --words)')
    parser.add_argument('--ai-calls', type=int, default=AI_CAP)
    parser.add_argument('--judge', type=int, default=JUDGE_PER_WORD)
    parser.add_argument('--days', type=int, default=WINDOW_DAYS)
    parser.add_argument('--sync', action='store_true', help='только отметить включённые одобренные слова')
    args = parser.parse_args(argv)
    if args.no_ai and not args.words and not args.sync:
        parser.error('--no-ai без --words: кандидатов предлагает AI')
    client = _client()
    hit, current = _matcher()
    if args.sync:
        applied, waiting = sync(client, current)
        print(json.dumps({'applied': [r['key'] for r in applied],
                          'waiting_commit': [r['key'] for r in waiting]}, ensure_ascii=False))
        return 0
    rows = ledger_misses(client)
    misses = keyword_misses(client, rows)
    words = [w for w in (args.words or '').split(',') if w.strip()] if args.words else None
    dropped = {}  # type: Dict[str, str]
    proposals = asyncio.run(propose_words(client, misses, current, hit, words=words, use_ai=not args.no_ai,
                                          ai_cap=args.ai_calls, judge=args.judge, days=args.days,
                                          dropped=dropped))
    proposals += channel_gaps(rows)
    if args.dry_run:
        for text, _, _ in messages(proposals):
            print(text)
            print('-' * 40)
        print(json.dumps({'misses_no_keyword': len(misses), 'proposals': len(proposals), 'dropped_words': dropped},
                         ensure_ascii=False))
        return 0
    fresh = save(client, proposals)
    sent = 0 if args.no_send else send(client, fresh)
    applied, waiting = sync(client, current)
    print(json.dumps({'misses_no_keyword': len(misses), 'proposals': len(proposals), 'dropped_words': dropped,
                      'new_or_unsent': len(fresh),
                      'sent': sent, 'applied': [r['key'] for r in applied],
                      'waiting_commit': [r['key'] for r in waiting]}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
