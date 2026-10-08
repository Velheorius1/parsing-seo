"""Недельный отчёт топ-100 (фаза 7 плана 07.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Детектор пропусков, предложения правок и ⭐-полоса работают
сами по себе; без отчёта Данияр не видит, учится ли система: растёт ли полнота,
что пропущено нового, как полоса показала себя на его кнопках, не замолчала ли
лента.

Что тут держится:
  • «новый пропуск» — нет в прошлом разборе, а не «договор за неделю»: журнал
    догружает договоры задним числом;
  • тихий ноль ленты — ⚠️, а не «пропусков нет»;
  • отметки Данияра считаются только по лотам «только по ⭐» — от них перевод в пуш;
  • текст укладывается в лимит Telegram, чужие строки экранированы.
"""
from datetime import datetime, timezone

from crawler.core import vip_lane as V
from crawler.scripts import customer_watch_weekly as W


def _row(feed, bid, kind, amount=50e6, awarded='2026-10-05', stage=None, inn='200829053'):
    return {'feed': feed, 'business_id': bid, 'miss_type': kind, 'amount_uzs': amount, 'awarded_on': awarded,
            'root_stage': stage, 'buyer_inn': inn, 'buyer_name': 'AT ALOQABANK', 'subject': 'Печать <бланков>',
            'winner_name': 'PRINT', 'source_url': 'https://etender.uzex.uz/lot/1'}


def test_new_miss_is_one_not_seen_in_the_previous_run_whatever_its_date():
    rows = [_row('deals', 1, 'A_filter', awarded='2026-09-01'), _row('deals', 2, 'A_filter', 900e6),
            _row('direct', 3, 'KNOWN_LOST'), _row('deals', 4, 'PRE_COVERAGE')]
    fresh = W.new_misses(rows, '2026-10-01', seen={('deals', '2')})
    assert [r['business_id'] for r in fresh] == [1], 'старый договор, впервые увиденный, — новый пропуск'
    assert [r['business_id'] for r in W.new_misses(rows, '2026-10-01')] == [2], 'без прошлого разбора — по дате'


def test_silent_feed_is_flagged():
    lines = W.feed_health({'deals': {'week': 0, 'usual': 300}, 'direct': {'week': 120, 'usual': 300},
                           'civil': {'week': 200, 'usual': 300}, 'ebirja_tender': {'week': 0, 'usual': 0}})
    assert lines[0].startswith('⚠️ ') and lines[1].startswith('⚠️ ')
    assert not lines[2].startswith('⚠️') and not lines[3].startswith('⚠️'), 'обычный ноль — не тревога'


def test_trend_compares_with_a_run_at_least_six_days_old():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    history = [{'generated_at': '2026-10-05T06:15:00+00:00', 'recall_announced': 0.37},
               {'generated_at': '2026-10-10T06:15:00+00:00', 'recall_announced': 0.5}]
    prev = W.previous(history, now)
    assert prev['recall_announced'] == 0.37
    assert W.delta(0.40, prev['recall_announced']) == ' (+3 п.п. за неделю)'
    assert W.delta(0.40, None) == ''


def test_star_stats_count_marks_only_on_star_only_lots():
    index = V.build_index({'entities': [{'id': 'c-1', 'name': 'AT ALOQABANK', 'inns': ['200829053'], 'rank': 7}]},
                          [], '2026-10-08')
    alerts = [{'alert_seq': 1, 'title': 'Печать', 'organization': 'AT ALOQABANK', 'telegram_message_id': 5},
              {'alert_seq': 2, 'title': 'Stend', 'organization': 'AT ALOQABANK'},
              {'alert_seq': 3, 'title': 'Табличка', 'organization': 'AT ALOQABANK'},
              {'alert_seq': 4, 'title': 'Печать', 'organization': 'CHUZHOY'}]
    hit = lambda a: 'Печать' in a['title']  # noqa: E731
    st = W.star_stats(alerts, index, hit, {1: 'client', 2: 'irrelevant'})
    assert (st['alerts'], st['starred'], st['starred_push'], st['vip_only']) == (4, 3, 1, 2)
    assert st['marked'] == {'не моё': 1} and st['unmarked'] == 1
    assert W.star_stats(alerts, None, hit, {})['starred'] == 0


def _ctx(**over):
    ctx = {'week_from': '2026-10-01', 'week_to': '2026-10-08', 'since': '2026-02-26',
           'summary': {'known': 28, 'missed': 179, 'types': {'B_no_announcement': 131, 'WON': 1},
                       'recall_announced': 0.368, 'recall_announced_uzs': 0.46, 'since': '2026-02-26'},
           'previous': None, 'new_misses': [_row('deals', 1, 'A_filter', stage='no_keyword')],
           'vip_mode': 'digest',
           'stars': {'alerts': 186, 'starred': 16, 'starred_push': 7, 'vip_only': 1, 'vip_only_push': 0,
                     'marked': {}, 'unmarked': 1},
           'proposals': {'new_or_unsent': 2, 'waiting_commit': []}, 'pending_decisions': 2,
           'feeds': ['⚠️ сделки etender 0 / 300'], 'feeds_to': '2026-10-04', 'unjudged': 0,
           'bitrix_line': 'Битрикс: выгрузка от 2026-10-07', 'rebuilt': None}
    ctx.update(over)
    return ctx


def test_text_is_escaped_short_and_has_every_section():
    text = W.render_text(_ctx())
    assert 'неделя 01.10–08.10' in text and 'знали 28 из 76 (37%)' in text and '131 покупка' in text
    assert '«Печать &lt;бланков&gt;»' in text and 'нет ключевого слова' in text
    assert 'только по ⭐ — 1' in text and 'ниже отдельным сообщением' in text and '⚠️ сделки' in text
    long = W.render_text(_ctx(new_misses=[_row('deals', i, 'A_filter') for i in range(40)],
                              feeds=['x' * 300] * 20))
    assert len(long) <= W.TEXT_LIMIT and '…и ещё 32' in long


def test_html_lists_every_customer_and_escapes():
    registry = {'entities': [{'id': 'c-200829053', 'name': 'AT <ALOQA>', 'inns': ['200829053'], 'rank': 1},
                             {'id': 'c-2', 'name': 'Quiet', 'inns': ['2'], 'rank': 2}]}
    page = W.render_html(registry, [_row('deals', 1, 'A_filter', stage='no_keyword'),
                                    _row('deals', 2, 'KNOWN_LOST')],
                         [{'id': 'c-200829053', 'recall': 0.5, 'recall_uzs': 0.5}], _ctx())
    assert 'AT &lt;ALOQA&gt;' in page and 'Печать &lt;бланков&gt;' in page
    assert 'отсёк фильтр: нет ключевого слова' in page and 'знали — выиграл другой' in page
    assert 'Quiet' in page and 'Покупок нашего профиля от 20 млн с 2026-02-26 нет' in page
