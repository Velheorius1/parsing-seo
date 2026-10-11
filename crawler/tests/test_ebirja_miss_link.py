"""Договоры ebirja «отбор» и «аукцион» сшиваются с нашим лотом по номеру процедуры (11.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Детектор пропусков все договоры ebirja, кроме э-магазина, писал
«площадку не собираем»: лотов отборов у нас не было, а у аукционов не было
номера процедуры. С #106 отборы собираются (external_id = номер процедуры),
у аукциона номер — в extra_info «Номер лота». Теперь такой договор получает
тот же разбор, что etender и ВМК-69: знали / отсёк фильтр / не собрали / до
нашего сбора — и пропуск «умер на словах» идёт в генератор слов.

Что тут держится:
  • отбор и тендер ebirja ищут лот среди «Ebirja Отборы и тендеры» по external_id,
    аукцион — среди «E-Birja активные аукционы» по «Номеру лота»;
  • договор раньше начала сшивки — «до нашего сбора», а не пропуск;
  • у аукционов в причине честно «сшиваем с договором», не «собираем»;
  • незнакомая площадка по-прежнему «площадку не собираем».
"""
from datetime import date
from types import SimpleNamespace

from crawler.scripts import customer_miss as M
from crawler.scripts import propose_fixes as P

COV = {'deals': date(2026, 2, 26), 'civil': date(2026, 9, 24),
       'ebirja_selection': date(2026, 10, 11), 'ebirja_tender': date(2026, 10, 11),
       'ebirja_auction': date(2026, 10, 11)}
NEVER_LATE = lambda first: False  # noqa: E731


def _p(feed, awarded='2026-11-20', procedure='26521012016108'):
    return {'feed': feed, 'business_id': '20625', 'awarded_on': awarded, 'winner_name': 'PRIMEPRESS',
            'amount_uzs': 6496e6, 'buyer_inn': '305122818', 'buyer_name': 'DASTURIY', 'procedure_id': procedure,
            'miss_type': 'A_filter', 'root_stage': 'no_keyword'}


def _lot(source, external_id, alert_seq=None, lot_number=None):
    return {'source': source, 'external_id': external_id, 'alert_seq': alert_seq,
            'extra_info': {M.LOT_NUMBER_KEY: lot_number} if lot_number else {},
            'created_at': '2026-11-01T00:00:00+00:00'}


def test_ebirja_contract_gets_the_same_verdicts_as_etender():
    lot = _lot(M.EBIRJA_SELECTION_SOURCE, '26521012016108')
    assert M.classify(_p('ebirja_selection'), [dict(lot, alert_seq=7)], {}, COV, NEVER_LATE) == ('KNOWN_LOST', None)
    assert M.classify(_p('ebirja_selection'), [lot], {}, COV, NEVER_LATE) == ('A_filter', None), 'стадию даст replay'
    assert M.classify(_p('ebirja_auction'), [], {}, COV, NEVER_LATE)[0] == 'A_not_collected'


def test_contract_before_linking_started_is_pre_coverage_not_uncrawled():
    kind, why = M.classify(_p('ebirja_selection', awarded='2026-07-08'), [], {}, COV, NEVER_LATE)
    assert (kind, why) == ('PRE_COVERAGE', 'лоты ebirja_selection собираем с 2026-10-11')
    kind, why = M.classify(_p('ebirja_auction', awarded='2026-09-28'), [], {}, COV, NEVER_LATE)
    assert kind == 'PRE_COVERAGE' and why == 'лоты ebirja_auction сшиваем с договором с 2026-10-11', \
        'аукционы собирали и раньше — не было номера процедуры'
    assert M.classify(_p('ebirja_tender', awarded='2026-07-08'), [], {}, {}, NEVER_LATE)[0] == 'PRE_COVERAGE'


def test_lots_are_found_by_procedure_number_in_their_own_source():
    sel = _lot(M.EBIRJA_SELECTION_SOURCE, '26521012016108')
    auc = _lot(M.EBIRJA_AUCTION_SOURCE, '891', lot_number='26521007040822')
    by_lot = {(M.EBIRJA_SELECTION_SOURCE, '26521012016108'): [sel],
              (M.EBIRJA_AUCTION_SOURCE, '26521007040822'): [auc]}
    assert M.ebirja_lots_of(_p('ebirja_selection'), by_lot) == [sel]
    assert M.ebirja_lots_of(_p('ebirja_tender'), by_lot) == [sel]
    assert M.ebirja_lots_of(_p('ebirja_auction', procedure='26521007040822'), by_lot) == [auc]
    assert M.ebirja_lots_of(_p('ebirja_auction'), by_lot) == [], 'номер отбора — не номер аукциона'
    assert M.ebirja_lots_of(_p('deals'), by_lot) == []


class _Tenders(object):
    """Таблица tenders: отборы — по external_id, аукционы — страницей с начала сшивки."""

    def __init__(self, rows):
        self.rows, self.calls, self.f = rows, [], {}

    def table(self, name):
        self.f = {}
        return self

    def select(self, cols):
        return self

    def eq(self, col, value):
        self.f[col] = value
        return self

    def in_(self, col, values):
        self.f[col] = list(values)
        self.calls.append(('in', col, list(values)))
        return self

    def gte(self, col, value):
        self.calls.append(('gte', col, value))
        return self

    def order(self, col):
        return self

    def range(self, a, b):
        return self

    def execute(self):
        out = [r for r in self.rows if r['source'] == self.f.get('source')
               and ('external_id' not in self.f or r['external_id'] in self.f['external_id'])]
        return SimpleNamespace(data=out)


def test_lot_rows_are_read_by_number_and_auctions_by_their_extra_info():
    sel = _lot(M.EBIRJA_SELECTION_SOURCE, '26521012016108')
    auc = _lot(M.EBIRJA_AUCTION_SOURCE, '891', lot_number='26521007040822')
    old_auc = _lot(M.EBIRJA_AUCTION_SOURCE, '757')      # без номера — до сшивки
    db = _Tenders([sel, auc, old_auc])
    by_lot = M.ebirja_lot_rows(db, [_p('ebirja_selection'), _p('ebirja_auction', procedure='26521007040822')])
    assert by_lot[(M.EBIRJA_SELECTION_SOURCE, '26521012016108')] == [sel]
    assert by_lot[(M.EBIRJA_AUCTION_SOURCE, '26521007040822')] == [auc]
    assert ('in', 'external_id', ['26521012016108']) in db.calls
    assert ('gte', 'created_at', '2026-10-11') in db.calls
    assert M.ebirja_lot_rows(_Tenders([]), []) == {}, 'нет договоров ebirja — нет запросов'


def test_word_proposals_learn_from_ebirja_misses_too(monkeypatch):
    lot = _lot(M.EBIRJA_SELECTION_SOURCE, '26521012016108')
    monkeypatch.setattr(M, 'ebirja_lot_rows', lambda client, rows: {(M.EBIRJA_SELECTION_SOURCE, '26521012016108'): [lot]})
    out = P.keyword_misses(None, [_p('ebirja_selection')])
    assert len(out) == 1 and out[0]['lot'] is lot
