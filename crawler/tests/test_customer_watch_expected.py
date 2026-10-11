"""«Ждём закупку»: что топ-100 покупал нашего в эти же недели год назад (11.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. 156 из 204 пропусков топ-100 (08.10) — покупки без объявления:
э-магазин ebirja и прямые договоры UZEX. Краулер их не увидит вовсе. Но 40%
покупок топ-100 повторяются у того же заказчика через год ±45 дней (замер 11.10:
96 из 237), и год назад в ближайшие 7 недель они купили нашего на 22,7 млрд.
Отчёт показывает, кого ждать, — успеть можно только заранее.

Что тут держится:
  • окно — [сегодня−365, +42 дня): ни днём раньше, ни днём позже;
  • только заказчики реестра, по заказчику — сумма, самые крупные первыми;
  • в тексте — не больше EXPECTED_SHOWN строк и лимит Telegram, чужое экранировано;
  • во вложении — все покупки окна со ссылкой.
"""
from datetime import date

from crawler.scripts import customer_watch_weekly as W
from crawler.tests.test_customer_watch_weekly import _ctx

TODAY = date(2026, 10, 11)
REGISTRY = {'entities': [{'id': 'c-1', 'name': 'UZBEKISTAN <AIRWAYS>', 'inns': ['1', '11'], 'rank': 3},
                         {'id': 'c-2', 'name': 'Минобороны', 'inns': ['2'], 'rank': 1}]}


def _buy(inn, awarded, amount, subject='Рекламно-сувенирная продукция'):
    return {'buyer_inn': inn, 'awarded_on': awarded, 'amount_uzs': amount, 'subject': subject,
            'winner_name': 'PRINT <MCHJ>', 'feed': 'deals', 'source_url': 'https://etender.uzex.uz/lot/9'}


def test_window_is_the_same_six_weeks_a_year_ago():
    rows = [_buy('1', '2025-10-11', 100e6),      # первый день окна
            _buy('1', '2025-11-21', 200e6),      # последний день окна (11.10 + 41)
            _buy('1', '2025-11-22', 999e6),      # уже вне окна
            _buy('1', '2025-10-10', 999e6),      # ещё вне окна
            _buy('9', '2025-10-20', 999e6)]      # не из реестра
    exp = W.expected(rows, REGISTRY, TODAY)
    assert [(g['id'], g['count'], g['amount_uzs']) for g in exp] == [('c-1', 2, 300e6)]


def test_customers_sorted_by_money_and_branches_merge():
    rows = [_buy('1', '2025-10-20', 3820e6), _buy('11', '2025-10-20', 1274e6),
            _buy('2', '2025-11-10', 380e6, 'Печать газеты ноябрь')]
    exp = W.expected(rows, REGISTRY, TODAY)
    assert [g['id'] for g in exp] == ['c-1', 'c-2']
    assert exp[0]['count'] == 2 and exp[0]['items'][0]['amount_uzs'] == 3820e6


def test_text_shows_who_to_expect_escaped_and_short():
    rows = [_buy('1', '2025-10-20', 3820e6), _buy('11', '2025-10-20', 1274e6),
            _buy('2', '2025-11-10', 380e6, 'Печать газеты <ноябрь>')]
    text = W.render_text(_ctx(expected=W.expected(rows, REGISTRY, TODAY)))
    assert 'Ждём закупку</b>: год назад в эти 6 недель топ-100 купили нашего на 5,5 млрд (3 покупки)' in text
    assert '• UZBEKISTAN &lt;AIRWAYS&gt; — «Рекламно-сувенирная продукция», 5,1 млрд за 2 покупки; 20.10' in text
    assert '«Печать газеты &lt;ноябрь&gt;»' in text and 'PRINT &lt;MCHJ&gt;' in text
    many = {'entities': [{'id': 'c-%d' % i, 'name': 'N%d' % i, 'inns': [str(i)]} for i in range(30)]}
    long = W.render_text(_ctx(expected=W.expected([_buy(str(i), '2025-10-20', 50e6) for i in range(30)], many, TODAY),
                              new_misses=[], feeds=['x' * 200] * 10))
    assert '…и ещё %d — во вложении' % (30 - W.EXPECTED_SHOWN) in long and len(long) <= W.TEXT_LIMIT
    assert 'Ждём закупку' not in W.render_text(_ctx()), 'нечего ждать — нет раздела'


def test_html_lists_every_expected_purchase_with_link():
    rows = [_buy('1', '2025-10-20', 3820e6), _buy('2', '2025-11-10', 380e6, 'Печать газеты <ноябрь>')]
    page = W.render_html(REGISTRY, [], [], _ctx(expected=W.expected(rows, REGISTRY, TODAY)))
    assert '<h2>Ждём закупку</h2>' in page and 'Печать газеты &lt;ноябрь&gt;' in page
    assert page.count('href="https://etender.uzex.uz/lot/9"') == 2 and 'сделки etender' in page
