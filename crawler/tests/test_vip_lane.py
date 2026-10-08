"""⭐-полоса топ-100 (фаза 6 плана 07.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Детектор пропусков: у заказчиков топ-100 лоты нашего профиля
умирают на словах — «Merchendayzing» Узбектелекома на 1,8 млрд, «Esdalik qo'l
soati» МВД на 1,9 млрд. Словарь все формы не догонит. Решение плана: лот
заказчика из топа идёт в AI и без слова, в алерте — ⭐ и кто у него выигрывал.

Что тут держится:
  • без индекса фильтр и его логи прежние — полоса не может тихо поменять поток;
  • заказчик узнаётся только точно: ИНН или имя, под которым этот ИНН покупал;
    общий для двух сущностей или короткий ключ не используется;
  • лот «только по ⭐» не обходит AI (UZEX-bypass) и в режиме digest не будит пушем;
  • в тени такие лоты не уходят ни в AI, ни в Telegram — только в журнал замера;
  • режим без файла / с мусором — off.
"""
import asyncio
import json
import logging
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.core import vip_lane as V  # noqa: E402
from crawler.core.models import RawTender  # noqa: E402

REGISTRY = {'entities': [
    {'id': 'c-200829053', 'name': 'AT ALOQABANK', 'inns': ['200829053'], 'rank': 7, 'segment': 'банк'},
    {'id': 'c-201055108', 'name': 'MVD (структуры)', 'inns': ['201055108', '201122919'], 'rank': 5},
]}
LEDGER = [
    {'buyer_inn': '200829053', 'buyer_name': '"ALOQABANK" ATB', 'winner_name': 'PRINT A', 'amount_uzs': 900e6,
     'profile': 'poly'},
    {'buyer_inn': '200829053', 'buyer_name': 'AT ALOQABANK', 'winner_name': 'PRINT B', 'amount_uzs': 300e6,
     'profile': 'merch'},
    {'buyer_inn': '200829053', 'buyer_name': 'AT ALOQABANK', 'winner_name': 'MEBEL', 'amount_uzs': 5e9,
     'profile': 'none'},
    {'buyer_inn': '200829053', 'buyer_name': 'AT ALOQABANK', 'winner_name': 'WINCH GROUP', 'amount_uzs': 2e9,
     'profile': 'poly'},
    {'buyer_inn': '201122919', 'buyer_name': 'Toshkent shahar IIBB', 'winner_name': 'X', 'amount_uzs': 1,
     'profile': 'poly'},
    {'buyer_inn': '201055108', 'buyer_name': 'Toshkent shahar IIBB', 'winner_name': 'Y', 'amount_uzs': 1,
     'profile': 'poly'},
    {'buyer_inn': '999', 'buyer_name': 'CHUZHOY', 'winner_name': 'Z', 'amount_uzs': 1, 'profile': 'poly'},
]


def _index():
    return V.build_index(REGISTRY, LEDGER, '2026-10-08', is_ours=lambda w: 'WINCH' in w)


def _tender(title, org='', extra=None, source='ETender UZEX', price=50e6):
    return RawTender(id=title, external_id=title, title=title, organization=org, price=price, currency='UZS',
                     source=source, search_text='', message_type='tender', extra_info=extra or {})


# ── индекс и сопоставление ───────────────────────────────────────────────────

def test_index_maps_inns_names_and_past_winners():
    idx = _index()
    assert idx['inns']['201122919'] == 'c-201055108'
    assert idx['aliases'][V.alias_key('"ALOQABANK" ATB')] == 'c-200829053'
    assert idx['entities']['c-200829053']['winners'] == ['PRINT A', 'PRINT B'], \
        'только наш профиль и без своих побед'
    assert V.alias_key('Toshkent shahar IIBB') in idx['aliases'], 'одна сущность под двумя ИНН — ключ однозначен'


def test_ambiguous_or_short_key_is_never_used():
    reg = {'entities': REGISTRY['entities'] + [{'id': 'c-1', 'name': 'AT ALOQABANK', 'inns': ['1']}]}
    idx = V.build_index(reg, [], '2026-10-08')
    assert V.alias_key('AT ALOQABANK') not in idx['aliases'], 'одно имя у двух сущностей'
    idx = V.build_index({'entities': [{'id': 'c-2', 'name': 'UZ AJ', 'inns': ['2']}]}, [], 'x')
    assert idx['aliases'] == {}, 'ключ короче 8 знаков'


def test_match_by_inn_or_normalized_name_only():
    idx = _index()
    assert V.match(_tender('x', extra={'ИНН заказчика': '201122919'}), idx) == 'c-201055108'
    assert V.match(_tender('x', extra={'customer_inn': '200829053'}), idx) == 'c-200829053'
    assert V.match(_tender('x', org='«Aloqabank» АТБ'), idx) == 'c-200829053', 'ёлочки и «АТБ» кириллицей'
    assert V.match(_tender('x', org="'ALOQABANK' atb"), idx) == 'c-200829053', 'кавычки и регистр не мешают'
    assert V.match(_tender('x', org='CHUZHOY'), idx) is None
    assert V.match(_tender('x', org='AT ALOQABANK'), None) is None


# ── фильтр ───────────────────────────────────────────────────────────────────

def test_prefilter_without_index_is_unchanged(caplog):
    batch = [_tender('Печать бланков', org='AT ALOQABANK'), _tender('Ремонт кровли', org='AT ALOQABANK')]
    with caplog.at_level(logging.INFO):
        plain = N.prefilter(batch, ['печать'], tnved_scope=[])
    assert [v.dropped_at for v in plain.verdicts] == [None, 'no_keyword']
    assert '[VIP]' not in caplog.text


def test_vip_lot_without_keyword_goes_to_ai_keyword_lot_keeps_its_word(caplog):
    batch = [_tender('Печать бланков', org='AT ALOQABANK'),
             _tender('Merchendayzing mahsulotlari', org='AT ALOQABANK'),
             _tender('Merchendayzing mahsulotlari', org='CHUZHOY')]
    with caplog.at_level(logging.INFO):
        res = N.prefilter(batch, ['печать'], tnved_scope=[], vip_index=_index())
    assert [v.matched_kw for v in res.verdicts] == ['печать', 'vip:c-200829053', None]
    assert [t.title for t, _ in res.matching] == ['Печать бланков', 'Merchendayzing mahsulotlari']
    assert '[VIP] 1 lots' in caplog.text


def test_vip_only_lot_never_bypasses_the_ai_gate():
    # Тот же лот предквалификации с «нишевым» словом в заголовке, но без ключевика:
    # обычный ушёл бы мимо AI, «только по ⭐» — нет.
    lot = _tender('Конверт-обложк', org='AT ALOQABANK', source='UZEX Предквалификации')
    res = N.prefilter([lot], ['zzz'], tnved_scope=[], vip_index=_index())
    assert res.uzex_bypass == [] and [kw for _, kw in res.matching] == ['vip:c-200829053']


def test_digest_mode_keeps_vip_only_lots_out_of_push():
    a, b = _tender('a', price=900e6), _tender('b', price=900e6)
    always = lambda t: True  # noqa: E731
    push, digest = N._split_routing([(a, 'печать'), (b, 'vip:c-1')], always, 'digest')
    assert [t.title for t, _ in push] == ['a'] and [t.title for t in digest] == ['b']
    push, digest = N._split_routing([(a, 'печать'), (b, 'vip:c-1')], always, 'push')
    assert len(push) == 2 and digest == []


# ── формат ───────────────────────────────────────────────────────────────────

def test_alert_gets_a_star_line_and_a_readable_hashtag():
    idx = _index()
    t = _tender('Merchendayzing mahsulotlari', org='AT ALOQABANK')
    text = N._format_alert(t, 'vip:c-200829053', vip=idx['entities']['c-200829053'])
    assert '⭐ Топ-100: AT ALOQABANK (место 7) · раньше выигрывали: PRINT A, PRINT B' in text
    assert text.rstrip().endswith('#топ100')
    plain = N._format_alert(t, 'печать')
    assert '⭐' not in plain and plain.rstrip().endswith('#печать')


def test_digest_line_is_starred():
    t = _tender('Merchendayzing', org='AT ALOQABANK')
    assert '*1.* ⭐ *Merchendayzing*' in N._build_digest_text([t], stars={(t.external_id, t.source): {}})
    assert '⭐' not in N._build_digest_text([t])


# ── режим и тень ─────────────────────────────────────────────────────────────

def test_mode_defaults_to_off(tmp_path):
    path = tmp_path / 'vip_lane.json'
    assert V.mode(path) == 'off'
    path.write_text('мусор')
    assert V.mode(path) == 'off'
    V.set_mode('shadow', 'Данияр', path)
    assert V.mode(path) == 'shadow' and json.loads(path.read_text())['set_by'] == 'Данияр'
    try:
        V.set_mode('loud', 'x', path)
        raise AssertionError('неизвестный режим должен падать')
    except ValueError:
        pass


def test_shadow_mode_logs_vip_lots_and_sends_nothing(monkeypatch):
    logged, posts = [], []
    monkeypatch.setattr(N, 'settings', types.SimpleNamespace(
        telegram_bot_token='t', telegram_alert_chat_id='1', openrouter_api_key='',
        ai_relevance_model='x', ai_relevance_model_fast=''))
    monkeypatch.setattr(N, '_get_keywords', lambda: ['печать'])
    monkeypatch.setattr(N, '_load_tnved_scope', lambda: [])
    monkeypatch.setattr(V, 'mode', lambda path=None: 'shadow')
    monkeypatch.setattr(V, 'load_index', lambda path=None: _index())
    monkeypatch.setattr(V, 'log_shadow', lambda items, path=None: logged.extend(items))

    class _NoHttp(object):
        def __init__(self, *a, **k):
            posts.append('client')
    monkeypatch.setattr(N.httpx, 'AsyncClient', _NoHttp)
    sent = asyncio.run(N.send_alerts([_tender('Merchendayzing mahsulotlari', org='AT ALOQABANK')]))
    assert sent == 0 and posts == []
    assert [(r['entity'], r['title']) for r in logged] == [('c-200829053', 'Merchendayzing mahsulotlari')]
