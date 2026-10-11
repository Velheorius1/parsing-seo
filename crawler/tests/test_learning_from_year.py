"""Словарь учится на всём, что заказчики купили за год, а не только на пропусках (11.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Предложения слов (фаза 5) брали только пропуски, где лот лежал
у нас в базе и умер на словах. Но язык заказчиков виден и в договорах без
нашего лота: 11.10 замер «словарь против договоров нашего профиля за год» нашёл
«Qimmatbaho sovg‘alar» Минобороны и «Poligrafiya mahsulotlari» Узбектелекома —
словарь видел 87% покупок (94% по деньгам). Теперь непокрытые договоры года —
второй вход генератора, а доля покрытия — метрика обучения в недельном отчёте.

Что тут держится:
  • доля покрытия — по числу и по деньгам, непокрытое — самое дорогое первым;
  • договор, который уже идёт пропуском с лотом, второй раз в выборку не входит;
  • у договора года нет лота: его судит предмет, replay его не видит;
  • слово только из года предлагается, если AI взял хоть один свежий лот окна
    (или окно пустое) — иначе до Данияра оно не довело бы ничего;
  • усечённый год — сбой вслух, а не ложная доля.
"""
import asyncio
from types import SimpleNamespace

import pytest

from crawler.scripts import customer_watch_weekly as W
from crawler.scripts import propose_fixes as P
from crawler.tests.test_customer_watch_weekly import _ctx
from crawler.tests.test_learning_proposals import _Replay, _hit, _miss


def _buy(i, subject, amount, feed='civil'):
    return {'feed': feed, 'business_id': i, 'buyer_name': 'B%d' % i, 'subject': subject, 'amount_uzs': amount}


def test_coverage_counts_purchases_and_money_and_sorts_the_rest():
    rows = [_buy(1, 'печать бланков', 600e6), _buy(2, 'Qimmatbaho sovg‘alar', 165e6),
            _buy(3, 'Газета 2-кв', 753e6), _buy(4, 'печать газет', 100e6)]
    cov, rest = P.dictionary_coverage(rows, _hit, ['печать'])
    assert (cov['total'], cov['covered'], cov['share']) == (4, 2, 0.5)
    assert cov['share_uzs'] == round(700e6 / 1618e6, 3) and cov['uncovered_uzs'] == 918e6
    assert [r['business_id'] for r in rest] == [3, 2], 'непокрытое — самое дорогое первым'
    assert P.dictionary_coverage([], _hit, ['печать'])[0]['share'] is None


def test_purchase_already_counted_as_a_miss_is_not_taken_twice():
    miss = _miss(1, 'нашр этиш', 576e6)
    miss['purchase'].update(feed='deals', business_id=174098)
    rest = [_buy(174098, 'нашр этиш', 576e6, feed='deals'), _buy(2, 'Газета', 753e6)]
    items = P.year_items(rest, [miss])
    assert [i['purchase']['business_id'] for i in items] == [2] and items[0]['lot'] is None
    many = [_buy(i, 'газета %d' % i, 30e6) for i in range(200)]
    assert len(P.year_items(many, [])) == P.YEAR_IN_PROMPT


def _run(monkeypatch, items, window, words):
    replay = _Replay()
    monkeypatch.setattr(P, '_replay', replay)
    monkeypatch.setattr(P, 'window_rows', lambda client, word, since: (window.get(word, []), False))
    dropped = {}
    out = asyncio.run(P.propose_words(None, items, ['печать'], _hit, words=words, days=14, dropped=dropped))
    return out, replay, dropped


def test_word_from_the_year_is_proposed_with_its_evidence(monkeypatch):
    year = [{'purchase': _buy(1, 'Газета 2-кв', 753e6), 'lot': None},
            {'purchase': _buy(2, 'ВАТАНПАРВАР газетаси', 238e6), 'lot': None}]
    window = {'газет': [{'id': 'w1', 'title': 'газета подписка', '_ai': True},
                        {'id': 'w2', 'title': 'газета объявлений', '_ai': False}]}
    out, replay, _ = _run(monkeypatch, year, window, ['газет'])
    assert [p['key'] for p in out] == ['газет']
    ev = out[0]['evidence']
    assert (ev['caught'], ev['missed_total'], ev['year_covered'], ev['year_total']) == (0, 0, 2, 2)
    assert ev['year_uzs'] == 991e6 and ev['year_lots'][0]['subject'] == 'Газета 2-кв'
    # У договора нет лота: replay судит только окно (фальшивый replay упал бы на None).
    assert sum(n for n, use_ai, _ in replay.calls if use_ai) == 2
    assert out[0]['backtest']['accepted'] == 1


def test_word_from_the_year_that_ai_never_takes_is_dropped(monkeypatch):
    year = [{'purchase': _buy(1, 'Баннер', 261e6), 'lot': None}]
    window = {'баннер': [{'id': 'w%d' % i, 'title': 'баннер %d' % i, '_ai': False} for i in range(3)]}
    out, _, dropped = _run(monkeypatch, year, window, ['баннер'])
    assert out == [] and dropped['баннер'].startswith('AI не взял ни одного из 3')


def test_rare_word_with_an_empty_window_is_still_proposed(monkeypatch):
    year = [{'purchase': _buy(1, 'Bezak ishlari va matbaa xizmatlari', 794e6), 'lot': None}]
    out, _, _ = _run(monkeypatch, year, {}, ['matbaa'])
    assert [p['key'] for p in out] == ['matbaa'] and out[0]['backtest']['judged'] == 0


def test_misses_and_year_together_keep_the_miss_numbers(monkeypatch):
    items = [_miss(1, 'газета нашр', 90e6), {'purchase': _buy(2, 'Газета', 753e6), 'lot': None}]
    out, _, _ = _run(monkeypatch, items, {}, ['газет'])
    ev = out[0]['evidence']
    assert (ev['caught'], ev['missed_total'], ev['delivered'], ev['year_covered']) == (1, 1, 1, 1)
    text = P.line(dict(out[0], id=4))
    assert 'пропусков ловит 1 из 1' in text and 'Ещё покрывает 1 покупку нашего профиля за год на 753 млн' in text


def test_year_only_line_says_coverage_not_misses():
    p = {'kind': 'keyword', 'key': 'газет', 'id': 5, 'payload': {'why': ''},
         'evidence': {'caught': 0, 'missed_total': 8, 'amount_uzs': 0, 'delivered': 0, 'ai_checked': True,
                      'lots': [], 'year_covered': 2, 'year_uzs': 991e6,
                      'year_lots': [{'buyer_name': 'Минобороны <MV>', 'subject': 'Газета 2-кв', 'amount_uzs': 753e6}]},
         'backtest': {'window_days': 14, 'passed_prefilter': 0, 'judged': 0}}
    text = P.line(p)
    assert 'пропусков ловит' not in text
    assert 'Покрывает 2 покупки нашего профиля за год на 991 млн' in text
    assert '«Газета 2-кв» (Минобороны &lt;MV&gt;), 753 млн' in text


def test_prompt_shows_year_contracts_by_subject():
    prompt = P.build_prompt([_miss(1, 'нашр этиш', 1e8), {'purchase': _buy(2, 'Газета 2-кв', 753e6), 'lot': None}],
                            ['печать'])
    assert '1. нашр этиш — ' in prompt and '2. Газета 2-кв — договор, B2' in prompt


class _Pages(object):
    """Клиент Supabase, у которого любая страница полная: год не кончается."""

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=[{'status': 'contract'}] * P.WINDOW_PAGE)


def test_truncated_year_fails_loudly():
    with pytest.raises(RuntimeError, match='выборка неполна'):
        P.year_purchases(_Pages())


def test_weekly_text_shows_dictionary_coverage_and_its_trend():
    cov = {'total': 699, 'covered': 610, 'share': 0.873, 'share_uzs': 0.94}
    text = W.render_text(_ctx(coverage=cov, coverage_prev={'share': 0.85}))
    assert 'Словарь видит 610 из 699 покупок нашего профиля за год (87%), по деньгам 94% (+2 п.п. за неделю)' in text
    assert 'Словарь видит' not in W.render_text(_ctx()), 'нет замера — нет строки, а не «0%»'


def test_miss_the_live_dictionary_already_catches_does_not_count(monkeypatch):
    """11.10: «sovg» «ловило» два пропуска UzAuto, которые после правки апострофа ловит «sovga»."""
    items = [_miss(1, 'печать подарков sovg', 269e6), _miss(2, 'sovg-набор', 92e6)]
    out, _, _ = _run(monkeypatch, items, {}, ['sovg'])
    ev = out[0]['evidence']
    assert (ev['caught'], ev['missed_total'], ev['amount_uzs']) == (1, 1, 92e6)
