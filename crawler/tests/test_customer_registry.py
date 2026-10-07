"""Реестр топ-100 заказчиков и клиенты Битрикса (фаза 3 плана 07.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Топ пересобирается раз в месяц, а решения Данияра —
закрепить заказчика, объединить структуры одной организации, связать с
компанией Битрикса — не должны теряться при пересборке. У компаний Битрикса нет
ИНН, названия бытовые («aloqabank», «NBU Bank»), поэтому связь только по имени
и только после «да».
"""
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

from crawler.core import customer_registry as CR
from crawler.scripts import customer_rank as R
from crawler.scripts import customer_registry_edit as E

TODAY = date(2026, 10, 7)


def _ranked(*rows):
    return [dict({'inns': [r[0]], 'inn': r[0], 'name': r[1], 'segment': 'банк', 'score': r[2],
                  'spend12': r[2], 'spend24': 0, 'purchases': 1, 'last': '2026-09-01'}) for r in rows]


def test_pinned_and_manual_fields_survive_monthly_rebuild():
    first = CR.build(_ranked(('200829053', 'AT ALOQABANK', 900), ('200836354', 'НАЦБАНК ВЭД', 100)),
                     CR.empty(), '2026-10-07', top=1)
    assert [e['name'] for e in first['entities']] == ['AT ALOQABANK']
    CR.pin(first, '200836354', 'НАЦБАНК ВЭД', 'Данияр', '2026-10-07')
    first['entities'][0]['own_site_url'] = 'https://aloqabank.uz'
    nxt = CR.build(_ranked(('200829053', 'AT ALOQABANK', 950), ('200836354', 'НАЦБАНК ВЭД', 50)),
                   first, '2026-11-07', top=1)
    by = {e['inns'][0]: e for e in nxt['entities']}
    assert by['200836354']['pinned'] and by['200836354']['rank'] == 2, "закреплённый вне топа остаётся"
    assert by['200829053']['own_site_url'] == 'https://aloqabank.uz' and by['200829053']['score'] == 950
    gone = CR.build(_ranked(('200829053', 'AT ALOQABANK', 950)), nxt, '2026-12-07', top=1)
    assert [e['rank'] for e in gone['entities'] if e['inns'] == ['200836354']] == [None], \
        "закреплённый без покупок — в реестре с rank None"


def test_confirmed_group_sums_its_inns_in_the_ranking():
    registry = CR.build(_ranked(('200541739', 'MUDOFAA ISHLAR BOSHQARMASI', 9), ('207327301', 'MUDOFA HUZURIDAGI', 8)),
                        CR.empty(), '2026-10-07')
    CR.merge(registry, ['200541739', '207327301'], 'Минобороны', 'Данияр', '2026-10-07')
    rows = [{'buyer_inn': inn, 'buyer_name': inn, 'amount_uzs': 100, 'awarded_on': '2026-09-01',
             'status': 'accepted', 'profile': 'poly', 'subject': 'Газета', 'feed': 'deals', 'winner_name': 'W'}
            for inn in ('200541739', '207327301')]
    result = R.rank(rows, TODAY, None, CR.groups(registry))
    assert len(result['entities']) == 1
    e = result['entities'][0]
    assert e['name'] == 'Минобороны' and e['inns'] == ['200541739', '207327301'] and e['score'] == 400


def test_merge_keeps_pin_and_bitrix_of_the_parts():
    registry = CR.build(_ranked(('200541739', 'A', 9), ('207327301', 'B', 8)), CR.empty(), '2026-10-07')
    CR.pin(registry, '207327301', 'B', 'Данияр', '2026-10-07')
    CR.link_bitrix(registry, {'id': 5, 'name': 'Минобороны'}, '200541739', 'Данияр', '2026-10-07')
    CR.add_proposal(registry, 'merge', {'inns': ['200541739', '207327301'], 'name': 'МО'})
    CR.merge(registry, ['200541739', '207327301'], 'МО', 'Данияр', '2026-10-08')
    (entity,) = registry['entities']
    assert entity['pinned'] and entity['bitrix'][0]['id'] == 5 and entity['merged_by'] == 'Данияр 2026-10-08'
    assert registry['merge_proposals'][0]['status'] == 'confirmed'


def test_one_inn_cannot_belong_to_two_customers():
    bad = {'entities': [{'id': 'a', 'name': 'A', 'inns': ['200541739']}, {'id': 'b', 'name': 'B', 'inns': ['200541739']}]}
    try:
        CR.validate(bad)
        raise AssertionError('принял дубль ИНН')
    except ValueError:
        pass


def test_bitrix_name_candidates_survive_transliteration_but_not_generic_words():
    registry = CR.build(_ranked(('200829053', 'AT ALOQABANK', 9), ('306628114', 'АО UZBEKISTAN AIRWAYS', 8),
                                ('207243390', 'AGROBANK ATB MARK.AMAL.BOSHQ', 7)), CR.empty(), '2026-10-07')
    registry['entities'].append({'id': 'c-201214240', 'name': 'UVD SAMARQAND VILOYATI', 'inns': ['201214240']})
    # Прогон 07.10: «Самарканд Регенси» и «спорт мастер» липли к УВД и спортакадемии по городу и слову.
    companies = [{'id': 93, 'name': 'Алокабанк'}, {'id': 463, 'name': 'aloqabank'},
                 {'id': 377, 'name': 'Uzbekistan Airways'}, {'id': 1, 'name': 'TBC bank'},
                 {'id': 151, 'name': 'Самарканд Регенси'}, {'id': 551, 'name': 'samarqand cognac'}]
    pairs = {(c['bitrix_id'], c['inn']) for c in CR.bitrix_candidates(registry, companies)}
    assert pairs == {(93, '200829053'), (463, '200829053'), (377, '306628114')}, pairs


def test_rejected_proposal_is_not_offered_again():
    registry = CR.empty()
    proposal = {'bitrix_id': 1, 'bitrix_name': 'x', 'inn': '200829053'}
    assert CR.add_proposal(registry, 'bitrix', proposal)
    assert CR.reject(registry, 'bitrix', '1:200829053', 'Данияр', '2026-10-07') == 1
    assert not CR.add_proposal(registry, 'bitrix', proposal), 'отклонённое предлагать каждый месяц нельзя'


def test_bitrix_export_counts_orders_and_carries_no_contacts():
    path = Path(tempfile.mkdtemp()) / 'snapshot' / 'winch_bitrix.sqlite'
    path.parent.mkdir()
    con = sqlite3.connect(str(path))
    con.executescript("""
        CREATE TABLE companies(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE deals(id INTEGER PRIMARY KEY, pipeline TEXT, stage TEXT, company_id INTEGER, created_iso TEXT);
        INSERT INTO companies VALUES (1, 'aloqabank');
        INSERT INTO deals VALUES (1, 'Производство', 'Заказ доставлен', 1, '2026-08-21T10:00:00'),
                                 (2, 'Производство', 'Заказ отменен', 1, '2026-09-30T10:00:00'),
                                 (3, 'Заявки', 'Оплата получена', 1, '2025-01-01T10:00:00'),
                                 (4, 'Заявки', 'Архив', 1, '2026-10-01T10:00:00');""")
    con.commit()
    con.close()
    data = E.bitrix_export(path)
    (company,) = data['companies']
    assert set(company) == {'id', 'name', 'orders', 'requests', 'last_order', 'last_deal'}
    assert company['orders'] == 2 and company['requests'] == 2
    assert company['last_order'] == '2026-08-21T10:00:00' and company['last_deal'] == '2026-10-01T10:00:00'


def test_html_marks_pinned_bitrix_clients_and_old_export():
    registry = CR.build(_ranked(('200829053', 'AT ALOQABANK', 9)), CR.empty(), '2026-10-07')
    CR.link_bitrix(registry, {'id': 463, 'name': 'aloqabank'}, '200829053', 'Данияр', '2026-10-07')
    CR.pin(registry, '200836354', 'НАЦБАНК ВЭД', 'Данияр', '2026-10-07')
    bitrix = {'as_of': '2026-08-01T00:00:00+00:00',
              'companies': [{'id': 463, 'name': 'aloqabank', 'orders': 4, 'last_order': '2026-08-21T12:49:56'}]}
    rows = [{'buyer_inn': '200829053', 'buyer_name': 'AT ALOQABANK', 'amount_uzs': 1e8, 'awarded_on': '2026-09-01',
             'status': 'accepted', 'profile': 'merch', 'subject': 'Сувениры', 'feed': 'deals', 'winner_name': 'W'}]
    page = R.render_html(R.rank(rows, TODAY, 100), [], TODAY, metrics={}, registry=registry, bitrix=bitrix)
    assert 'клиент: 4 заказ(ов), посл. 2026-08' in page
    assert 'Закреплённые вне топа' in page and 'НАЦБАНК ВЭД' in page
    assert 'старше 45 дней' in page, 'выгрузка 67 дней назад'
    assert 'старше' not in R.bitrix_line(dict(bitrix, as_of='2026-10-01'), TODAY)


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
