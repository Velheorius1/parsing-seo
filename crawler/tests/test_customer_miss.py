"""Детектор пропусков топ-100 (фаза 4 плана 07.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Данияр: «проверять не только, кто из конкурентов выиграл, но и
кто из заказчиков кому-то заказал, а мы это не узнали». Каждая покупка нашего
профиля получает вид: знали (выиграли / алертили до срока / человек отклонил)
или пропуск (гейт отсёк, лот не собран, алерт опоздал, объявления нет, площадку
не собираем). Покупка раньше нашего сбора — не пропуск и в полноту не входит.
"""
import sys
from datetime import date

from crawler.core import customer_registry as CR
from crawler.scripts import customer_miss as M

COV = {'deals': date(2026, 2, 26), 'civil': date(2026, 9, 24)}
NEVER_LATE = lambda first: False  # noqa: E731
ALWAYS_LATE = lambda first: True  # noqa: E731


def _p(feed='deals', awarded='2026-08-01', winner='PRINT MCHJ', amount=50e6, inn='200829053'):
    return {'feed': feed, 'business_id': '1', 'awarded_on': awarded, 'winner_name': winner,
            'amount_uzs': amount, 'buyer_inn': inn, 'buyer_name': 'AT ALOQABANK'}


def _lot(alert_seq=None, category=None, score=None):
    return {'source': 'ETender UZEX', 'alert_seq': alert_seq, 'relevance_category': category,
            'relevance_score': score, 'created_at': '2026-07-20T00:00:00+00:00'}


def test_alerted_before_deadline_is_known_not_a_miss():
    assert M.classify(_p(), [_lot(alert_seq=5)], {}, COV, NEVER_LATE) == ('KNOWN_LOST', None)


def test_alert_after_deadline_is_a_late_miss():
    assert M.classify(_p(), [_lot(alert_seq=5)], {}, COV, ALWAYS_LATE)[0] == 'A_late'


def test_human_rejected_alert_counts_as_known():
    assert M.classify(_p(), [_lot(alert_seq=5)], {5: 'irrelevant'}, COV, NEVER_LATE)[0] == 'KNOWN_REJECTED'


def test_lot_in_base_without_alert_needs_gate_stage_unless_ai_said_ours():
    assert M.classify(_p(), [_lot()], {}, COV, NEVER_LATE) == ('A_filter', None), 'стадию даст replay'
    lost = [_lot(category='client', score=90)]
    assert M.classify(_p(), lost, {}, COV, NEVER_LATE) == ('A_filter', 'delivery_loss')


def test_no_lot_row_is_a_miss_only_after_our_collection_started():
    assert M.classify(_p(awarded='2026-08-01'), [], {}, COV, NEVER_LATE)[0] == 'A_not_collected'
    assert M.classify(_p(awarded='2026-03-05'), [], {}, COV, NEVER_LATE)[0] == 'PRE_COVERAGE', \
        'договор через неделю после старта сбора — лот вышел раньше'
    # ВМК-69 собираем с 24.09.2026: итог 02.10 без нашей строки — ещё не пропуск.
    assert M.classify(_p(feed='civil', awarded='2026-10-02'), [], {}, COV, NEVER_LATE)[0] == 'PRE_COVERAGE'


def test_direct_and_eshop_have_no_announcement_ebirja_tenders_are_uncrawled():
    assert M.classify(_p(feed='direct'), [], {}, COV, NEVER_LATE)[0] == 'B_no_announcement'
    assert M.classify(_p(feed='ebirja_shop'), [], {}, COV, NEVER_LATE)[0] == 'B_no_announcement'
    assert M.classify(_p(feed='ebirja_selection'), [], {}, COV, NEVER_LATE)[0] == 'C_uncrawled_platform'


def test_our_win_is_won_whatever_the_feed():
    from crawler.core.outcome import OWN_ORG_FRAGMENTS
    ours = 'MCHJ %s' % sorted(OWN_ORG_FRAGMENTS)[0].upper()
    assert M.classify(_p(feed='direct', winner=ours), [], {}, COV, NEVER_LATE)[0] == 'WON'


def test_recall_by_count_money_and_announced_only():
    registry = {'entities': [{'id': 'c-200829053', 'name': 'AT ALOQABANK', 'inns': ['200829053']}]}
    rows = [dict(_p(amount=300e6), miss_type='KNOWN_LOST'), dict(_p(amount=100e6), miss_type='A_filter'),
            dict(_p(feed='direct', amount=100e6), miss_type='B_no_announcement'),
            dict(_p(amount=900e6), miss_type='PRE_COVERAGE')]
    (customer,) = M.recall(rows, registry)
    assert customer['purchases'] == 3, 'до нашего сбора — не в счёт'
    assert customer['recall'] == round(1 / 3, 3) and customer['recall_uzs'] == 0.6
    assert customer['recall_announced'] == 0.5, 'без прямых договоров: 1 из 2'
    assert customer['types'] == {'KNOWN_LOST': 1, 'A_filter': 1, 'B_no_announcement': 1}


def test_group_members_add_up_to_one_customer():
    registry = {'entities': [{'id': 'c-1', 'name': 'МВД (структуры)', 'inns': ['200637696', '202522965']}]}
    rows = [dict(_p(inn='200637696'), miss_type='KNOWN_LOST'), dict(_p(inn='202522965'), miss_type='A_late')]
    (customer,) = M.recall(rows, registry)
    assert customer['name'] == 'МВД (структуры)' and customer['purchases'] == 2 and customer['recall'] == 0.5


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print('PASS', test.__name__)
        except Exception as exc:
            print('FAIL', test.__name__, '-', repr(exc)[:200])
            failures += 1
    print('\n%d/%d passed' % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
