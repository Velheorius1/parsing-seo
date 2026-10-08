"""Предложения правок фильтра с бэктестом и кнопкой «да» (фаза 5 плана 07.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Детектор пропусков первым прогоном нашёл 33 покупки топ-100
нашего профиля, чей лот лежал у нас в базе и умер на словах: «чоп этиш»
кириллицей, «нашр», «matbaa». Решение Данияра: система ПРЕДЛАГАЕТ правку с
бэктестом, включает он кнопкой; сама ничего не включает.

Что тут держится:
  • слово, которое не довело бы до Данияра ни одного пропуска, не предлагается;
  • шум меряется вне выборки: пропущенные лоты, уже алертнутые строки и то, что
    ловит живой словарь, в «новые совпадения» окна не входят;
  • AI не тратится сверх бюджета прогона;
  • кнопка «да / нет» принимается только от владельца личного чата алертов,
    второй клик не перезаписывает первое решение;
  • в тексте предложения чужие строки экранированы (HTML-режим Telegram).
"""
import asyncio
from types import SimpleNamespace

from crawler.core import learning as L
from crawler.core.purchase_profile import rule_verdict
from crawler.scripts import propose_fixes as P


# ── кнопки и решение ─────────────────────────────────────────────────────────

def test_callback_roundtrip_and_garbage():
    assert L.parse(L.callback_data(12, 'ok')) == (12, 'ok')
    assert L.parse('lp:12:no') == (12, 'no')
    for junk in (None, '', 'lp:12', 'lp:x:ok', 'lp:-3:ok', 'lp:0:ok', 'lp:12:maybe', 'fb:12:ok', 'lp:1:ok:2'):
        assert L.parse(junk) is None, junk


def test_only_private_chat_owner_or_explicit_list_may_decide():
    assert L.approvers('123456') == {123456}, 'личный чат: id чата = id владельца'
    assert L.approvers('-100200300') == set(), 'групповой чат никого не даёт'
    assert L.approvers('-100200300', ' 77, x ,88') == {77, 88}
    assert L.approvers(None, '') == set()


class _Q(object):
    """Минимальный клиент Supabase: запоминает фильтры, отдаёт заданные строки."""

    def __init__(self, rows, listing=None):
        self.rows, self.listing, self.calls, self._select = rows, listing, [], False

    def table(self, name):
        self.calls.append(('table', name))
        self._select = False
        return self

    def update(self, fields):
        self.calls.append(('update', fields))
        return self

    def select(self, cols):
        self.calls.append(('select', cols))
        self._select = True
        return self

    def eq(self, col, value):
        self.calls.append(('eq', col, value))
        return self

    def execute(self):
        if self._select and self.listing is not None:
            return SimpleNamespace(data=self.listing)
        return SimpleNamespace(data=self.rows)


def test_decision_is_written_only_while_proposal_waits():
    db = _Q([{'id': 5, 'kind': 'keyword', 'key': 'нашр', 'status': 'approved'}])
    status, row = L.decide(db, 5, 'ok', '1 dan')
    assert status == 'approved' and row['key'] == 'нашр'
    assert ('eq', 'status', 'proposed') in db.calls, 'второй клик не должен перезаписать первое решение'
    assert ('eq', 'id', 5) in db.calls
    assert L.decide(_Q([]), 5, 'no', '1 dan') == (None, None)


def test_approved_word_promises_a_commit_not_an_instant_switch():
    assert 'коммитом' in L.ack_text('approved', {'kind': 'keyword'})
    assert 'коммитом' not in L.ack_text('approved', {'kind': 'channel'})
    assert L.ack_text('rejected', {'kind': 'keyword'}) == 'Отклонено'


def test_approved_words_are_applied_only_when_live():
    rows = [{'id': 1, 'kind': 'keyword', 'key': 'нашр', 'status': 'approved'},
            {'id': 2, 'kind': 'keyword', 'key': 'matbaa', 'status': 'approved'},
            {'id': 3, 'kind': 'channel', 'key': 'direct', 'status': 'approved'}]
    applied, waiting = L.pending_keywords(rows, ['печать', 'Нашр '])
    assert [r['id'] for r in applied] == [1] and [r['id'] for r in waiting] == [2]


def _bot(monkeypatch, chat_id):
    import crawler.scripts.feedback_bot as B
    answers, edits = [], []
    monkeypatch.setattr(B, 'settings', SimpleNamespace(telegram_alert_chat_id=chat_id))
    monkeypatch.setattr(B, 'answer_callback', lambda cid, text: answers.append(text))
    monkeypatch.setattr(B, 'set_markup', lambda chat, mid, kb: edits.append((chat, mid, kb)))
    monkeypatch.delenv('LEARNING_APPROVER_IDS', raising=False)
    return B, answers, edits


def _cq(user_id, data='lp:5:ok'):
    return {'callback_query': {'id': 'c1', 'data': data, 'from': {'id': user_id, 'username': 'dan'},
                               'message': {'chat': {'id': 42}, 'message_id': 9,
                                           'reply_markup': {'inline_keyboard': L.keyboard(5)}}}}


def test_bot_refuses_a_stranger_without_touching_the_db(monkeypatch):
    B, answers, edits = _bot(monkeypatch, '42')
    db = _Q([{'id': 5}])
    monkeypatch.setattr(B, '_learning_db', lambda: db)
    B.process_callback(_cq(user_id=999))
    assert answers and 'только' in answers[0]
    assert db.calls == [] and edits == []


def test_bot_records_owner_decision_and_rebuilds_keyboard_from_db(monkeypatch):
    B, answers, edits = _bot(monkeypatch, '42')
    listing = [{'id': 5, 'kind': 'keyword', 'key': 'нашр', 'status': 'approved'},
               {'id': 6, 'kind': 'channel', 'key': 'ebirja_shop', 'status': 'proposed'},
               {'id': 4, 'kind': 'keyword', 'key': 'чоп', 'status': 'rejected'}]
    db = _Q([{'id': 5, 'kind': 'keyword', 'key': 'нашр', 'status': 'approved'}], listing)
    monkeypatch.setattr(B, '_learning_db', lambda: db)
    B.process_callback(_cq(user_id=42))
    update = [c for c in db.calls if c[0] == 'update'][0][1]
    assert update['status'] == 'approved' and update['decided_by'] == '42 dan'
    assert ('eq', 'telegram_message_id', 9) in db.calls, 'клавиатура — по всем предложениям этого сообщения'
    assert 'коммитом' in answers[0]
    (chat, mid, kb), = edits
    assert (chat, mid) == (42, 9)
    assert kb == [[{'text': '❌ Отклонено · чоп', 'callback_data': 'done'}],
                  [{'text': '✅ Одобрено · нашр', 'callback_data': 'done'}],
                  [{'text': '✅ Э-магазин ebirja', 'callback_data': 'lp:6:ok'},
                   {'text': '❌ Э-магазин ebirja', 'callback_data': 'lp:6:no'}]]


def test_stale_message_is_repaired_even_when_already_decided(monkeypatch):
    """08.10: быстрые клики затёрли друг друга — в базе одобрено, в чате кнопки."""
    B, answers, edits = _bot(monkeypatch, '42')
    db = _Q([], [{'id': 5, 'kind': 'channel', 'key': 'ebirja_shop', 'status': 'approved'}])
    monkeypatch.setattr(B, '_learning_db', lambda: db)
    B.process_callback(_cq(user_id=42, data='lp:5:no'))
    assert answers == ['Уже решено']
    assert edits[0][2] == [[{'text': '✅ Одобрено · Э-магазин ebirja', 'callback_data': 'done'}]]


# ── кандидаты и бэктест ──────────────────────────────────────────────────────

def test_word_already_covered_by_the_live_dictionary_is_not_proposed():
    hit, current = P._matcher()
    assert 'kitob' in current
    assert P.normalize_word('Kitobi', current, hit) is None, '«kitobi» ловит стоящее «kitob»'
    assert P.normalize_word('печать', current, hit) is None
    assert P.normalize_word('  МАТБАА ', current, hit) == 'матбаа'
    assert P.normalize_word('нашри', current, hit) is None, '«нашр» в словаре с 08.10'
    for junk in ('ab', '2026', 'нашр,чоп', 'x' * 31, 'нашр;drop', ''):
        assert P.normalize_word(junk, current, hit) is None, junk


def test_candidates_come_from_ai_json_and_survive_fences():
    text = '```json\n{"candidates": [{"keyword": "нашр", "lots": [1], "why": "издание"}, {"x": 1}]}\n```'
    assert P.parse_candidates(text) == [{'keyword': 'нашр', 'why': 'издание'}]
    assert P.parse_candidates('не JSON') == []


def _hit(row, words):
    text = ('%s %s' % (row.get('title') or '', row.get('search_text') or '')).lower()
    return any(w in text for w in words)


def _miss(i, title, amount, pf=True, ai=True):
    return {'purchase': {'buyer_name': 'B%d' % i, 'subject': title, 'amount_uzs': amount},
            'lot': {'id': 'm%d' % i, 'title': title, '_pf': pf, '_ai': ai}}


class _Replay(object):
    def __init__(self):
        self.calls = []

    async def __call__(self, rows, use_ai, keywords):
        self.calls.append((len(rows), use_ai, list(keywords)))
        out = []
        for r in rows:
            pf = r.get('_pf', True)
            delivered = (r.get('_ai', True) if use_ai else None) if pf else False
            out.append(SimpleNamespace(passed_prefilter=pf, delivered=delivered, ai_error=False,
                                       dropped_at_stage=None if pf else 'price'))
        return out


def _run(monkeypatch, misses, window, words, ai_cap=150, judge=10, use_ai=True):
    replay = _Replay()
    monkeypatch.setattr(P, '_replay', replay)
    monkeypatch.setattr(P, 'window_rows', lambda client, word, since: (window.get(word, []), False))
    out = asyncio.run(P.propose_words(None, misses, ['печать'], _hit, words=words, use_ai=use_ai,
                                      ai_cap=ai_cap, judge=judge, days=14))
    return out, replay


def test_word_that_catches_nothing_or_only_ai_rejects_is_dropped(monkeypatch):
    misses = [_miss(1, 'нашр этиш хизмати', 576e6), _miss(2, 'матбаа хизмати', 90e6, ai=False)]
    out, _ = _run(monkeypatch, misses, {}, ['нашр', 'матбаа', 'esdalik'])
    assert [p['key'] for p in out] == ['нашр'], 'esdalik ничего не ловит, матбаа отсеял бы AI'
    ev = out[0]['evidence']
    assert (ev['caught'], ev['delivered'], ev['missed_total'], ev['amount_uzs']) == (1, 1, 2, 576e6)


def test_window_noise_is_measured_outside_the_tuning_sample(monkeypatch):
    misses = [_miss(1, 'нашр этиш хизмати', 576e6)]
    window = {'нашр': [
        {'id': 'm1', 'title': 'нашр этиш хизмати'},                     # сам пропуск — не шум
        {'id': 'w1', 'title': 'нашр печать', 'alert_seq': None},        # ловит живое «печать»
        {'id': 'w2', 'title': 'нашр', 'alert_seq': 77},                 # уже алертнут
        {'id': 'w3', 'title': 'нашриёт китоби', '_ai': True},
        {'id': 'w4', 'title': 'нашр тўплами', '_ai': False},
        {'id': 'w5', 'title': 'нашр қилиш', '_pf': False},             # отсеет цена
        {'id': 'w6', 'title': 'кашрут'},                                # ilike шире гейта
    ]}
    out, _ = _run(monkeypatch, misses, window, ['нашр'])
    bt = out[0]['backtest']
    assert (bt['matched'], bt['new'], bt['passed_prefilter']) == (6, 3, 2)
    assert (bt['judged'], bt['accepted'], bt['dropped']) == (2, 1, {'price': 1})
    assert bt['est_alerts_week'] == P.estimate_week(2, 2, 1, 14) == 0.5
    assert bt['accepted_examples'] == ['нашриёт китоби'] and bt['rejected_examples'] == ['нашр тўплами']


def test_ai_budget_is_never_exceeded(monkeypatch):
    misses = [_miss(i, 'нашр %d' % i, 30e6) for i in range(3)]
    window = {'нашр': [{'id': 'w%d' % i, 'title': 'нашр w%d' % i} for i in range(20)]}
    out, replay = _run(monkeypatch, misses, window, ['нашр'], ai_cap=5)
    assert sum(n for n, use_ai, _ in replay.calls if use_ai) <= 5
    assert out[0]['backtest']['judged'] == 2, '3 на пропуски + 2 на окно'
    out, replay = _run(monkeypatch, misses, window, ['нашр'], ai_cap=2)
    assert sum(n for n, use_ai, _ in replay.calls if use_ai) <= 2
    assert out[0]['evidence']['ai_checked'] is False, 'на все пропуски не хватает — их AI не судит вовсе'


def test_estimate_needs_a_judged_sample():
    assert P.estimate_week(10, 0, 0, 14) is None
    assert P.estimate_week(28, 10, 5, 14) == 7.0


def test_channel_gaps_sum_money_per_feed():
    rows = [{'feed': 'ebirja_shop', 'miss_type': 'B_no_announcement', 'buyer_name': 'AGMK', 'winner_name': 'X',
             'amount_uzs': 300e6},
            {'feed': 'ebirja_shop', 'miss_type': 'B_no_announcement', 'buyer_name': 'AGMK', 'winner_name': 'Y',
             'amount_uzs': 200e6},
            {'feed': 'direct', 'miss_type': 'B_no_announcement', 'buyer_name': 'MinEdu', 'winner_name': 'Z',
             'amount_uzs': 900e6},
            {'feed': 'deals', 'miss_type': 'A_filter', 'amount_uzs': 5e9}]
    gaps = P.channel_gaps(rows)
    assert [g['key'] for g in gaps] == ['direct', 'ebirja_shop']
    shop = gaps[1]['evidence']
    assert shop['count'] == 2 and shop['amount_uzs'] == 500e6 and shop['buyers'] == [('AGMK', 500e6)]


def _kw(pid, key, why='<i>издание</i>'):
    return {'kind': 'keyword', 'key': key, 'id': pid, 'payload': {'why': why},
            'evidence': {'caught': 1, 'missed_total': 33, 'amount_uzs': 576e6, 'delivered': 1, 'ai_checked': False,
                         'lots': [{'buyer_name': '"FVV" <MCHJ>', 'subject': 'Нашр & co', 'amount_uzs': 576e6}]},
            'backtest': {'window_days': 14, 'new': 11, 'passed_prefilter': 2, 'judged': 0}}


def test_line_escapes_foreign_text_and_says_when_ai_did_not_check():
    text = P.line(_kw(7, 'нашр'))
    assert '&lt;MCHJ&gt;' in text and 'Нашр &amp; co' in text and '<MCHJ>' not in text
    assert 'AI не проверял' in text and '#7 «нашр»' in text
    gap = P.channel_gaps([{'feed': 'ebirja_selection', 'miss_type': 'C_uncrawled_platform',
                           'buyer_name': 'U', 'winner_name': 'W', 'amount_uzs': 632e6}] * 2)[0]
    assert '2 покупки на 1,3 млрд' in P.line(dict(gap, id=8))


def test_one_message_per_kind_with_a_button_row_per_proposal():
    gap = dict(P.channel_gaps([{'feed': 'direct', 'miss_type': 'B_no_announcement', 'buyer_name': 'M',
                                'winner_name': 'W', 'amount_uzs': 9e8}])[0], id=9)
    msgs = P.messages([_kw(7, 'нашр'), gap, _kw(8, 'чоп')])
    assert [ids for _, _, ids in msgs] == [[7, 8], [9]]
    text, rows, _ = msgs[0]
    assert text.startswith(P.HEADERS['keyword'])
    assert rows[1] == [{'text': '✅ чоп', 'callback_data': 'lp:8:ok'}, {'text': '❌ чоп', 'callback_data': 'lp:8:no'}]
    assert all(L.parse(b['callback_data']) for row in rows for b in row)
    dry = dict(_kw(7, 'нашр')); dry.pop('id')
    assert P.messages([dry])[0][2] == [None], 'сухой прогон: строки в базе ещё нет — не падать'


def test_long_batch_is_split_under_the_telegram_limit():
    many = [_kw(i, 'слово%s' % chr(1072 + i)) for i in range(1, 30)]
    msgs = P.messages(many)
    assert len(msgs) > 1 and all(len(text) <= P.MESSAGE_LIMIT for text, _, _ in msgs)
    assert sum(len(ids) for _, _, ids in msgs) == 29


def test_judge_sends_cardholders_to_ai_instead_of_no_stem():
    """Наша победа Xalq Bank (753 млн) ушла в «нет корня» — корня «kartholder» не было."""
    row = {'feed': 'deals', 'subject': 'Mastercard kartalari uchun kartholder ishlab chiqarish xizmatlarini xarid qilish'}
    assert rule_verdict(row) == (None, 'ai')
    assert rule_verdict({'feed': 'deals', 'subject': 'Изготовление картхолдеров'}) == (None, 'ai')
    assert rule_verdict({'feed': 'deals', 'subject': 'Kartrij va toner'}) == ('none', 'no_stem')


def test_empty_ai_answer_is_reported_not_silent(monkeypatch):
    """07.10: рассуждение модели съело лимит, ответ пустой — прогон тихо дал ноль слов."""
    async def ask(prompt):
        return ''
    dropped = {}
    out = asyncio.run(P.propose_words(None, [_miss(1, 'нашр', 1e8)], ['печать'], _hit, ask=ask, dropped=dropped))
    assert out == [] and dropped == {'—': 'AI не дал ни одного кандидата'}


def test_general_word_is_not_proposed_and_reasons_survive_every_word(monkeypatch):
    """08.10: «xarid» (закупка) ≈150 лотов/нед на AI, 0 из 10 взято — предложено с «≈0 алертов».
    И причины отсева терялись после первого слова: локальный Counter затирал словарь."""
    misses = [_miss(1, 'нашр хизмати xarid', 576e6), _miss(2, 'чоп этиш', 90e6, ai=False)]
    window = {'нашр': [{'id': 'w1', 'title': 'нашр'}],
              'xarid': [{'id': 'x%d' % i, 'title': 'xarid %d' % i} for i in range(60)]}
    replay = _Replay()
    monkeypatch.setattr(P, '_replay', replay)
    monkeypatch.setattr(P, 'window_rows', lambda client, word, since: (window.get(word, []), False))
    dropped = {}
    out = asyncio.run(P.propose_words(None, misses, ['печать'], _hit, words=['нашр', 'xarid', 'чоп'],
                                      ai_cap=150, judge=10, days=14, dropped=dropped))
    assert [p['key'] for p in out] == ['нашр']
    assert dropped['xarid'].startswith('слишком общее: ≈30 лотов')
    assert dropped['чоп'].startswith('пойманное (1)'), 'причина после первого слова не потерялась'
    assert out[0]['backtest']['dropped'] == {}
