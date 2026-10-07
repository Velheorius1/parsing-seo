"""Журнал закупок по ИНН заказчика (purchase_ledger).

ИЗ ЧЕГО ВЫРОСЛО. Топ-100 заказчиков и «купили наше мимо нас» (07.10.2026) нельзя
считать по алертам: у половины нет заказчика, «КИТАЙ» в заказчиках, один лот в
трёх лентах. Журнал строится из договоров, где ИНН заказчика есть, — и не должен
сам портить данные: повторный upsert списка не затирает карточку, отказ и
расторжение не считаются покупкой.
"""
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from crawler.core import purchase_ledger as L
from crawler.scripts import purchase_backfill as B

DEAL = {"deal_date": "2026-10-06T22:05:09", "deal_id": 176246, "trade_id": 508717, "display_no": "26120012508717",
        "start_cost": 1788864000.0, "deal_cost": 1162761600.0, "currency_name": "Сум",
        "customer_name": "ASAKABANK", "customer_type_name": "Korporativ buyurtmachi", "customer_inn": "201589828",
        "provider_name": "PRIME ADVERTISING", "provider_inn": "312499234",
        "category_name": "LED monitorlarda reklama roliklarini joylashtirish",
        "deal_status_name": "Ғолиб томонидан қабул қилинган (Амалга ошган)"}
CIVIL = {"civil_contract_id": 17845, "civil_deal_id": 20556, "display_id": "26120500017845",
         "customer_inn": "202361383", "customer_name": "\"JAZONI IJRO ETISH BO`LIMI\"",
         "provider_inn": "308057792", "provider_name": "ALWAYS STEP FORWARD MCHJ", "civil_name": "Бланки",
         "cost": 20798400, "result_cost": 20000064, "currency_name": "Сом", "deal_date": "2026-10-06T00:00:00",
         "status_name": "Сделка совершена"}
DIRECT = {"id": 4446205, "display_id": "261200994446205", "category_name": "Услуги издательские",
          "provider_name": "ARBAXUM GROUP", "provider_inn": "305000001", "customer_name": "ИМОМ ТЕРМИЗИЙ",
          "customer_inn": "201000002", "contract_sum": 3600000.0, "currency_name": "UZS",
          "contract_num": "09", "contract_date": "2026-10-06T00:00:00", "status_name": "Опубликован",
          "typ_direct_purchase_name": "Прямые закупки"}


def test_deal_row_keeps_buyer_inn_subject_and_lot_link():
    row = L.from_uzex("deals", DEAL, "sha")
    assert row["buyer_inn"] == "201589828" and row["buyer_name"] == "ASAKABANK"
    assert row["winner_inn"] == "312499234"
    assert row["subject"].startswith("LED monitorlarda") and row["subject_hash"]
    assert row["amount_uzs"] == "1162761600" and row["start_amount"] == "1788864000"
    assert row["lot_key"] == "508717", "ключ сшивки с /lot/<id> наших строк etender"
    assert row["awarded_on"] == "2026-10-06" and row["status"] == "accepted"


def test_uzbek_deal_statuses_are_not_unknown():
    status = lambda text: L.purchase_status("deals", {"deal_status_name": text})
    assert status("Баённома шакллантирилган") == "protocol"
    assert status("Буюртмачи томонидан рад етилган") == "rejected"
    assert status("Шартнома бекор қилинган") == "cancelled"
    assert "rejected" not in L.PURCHASE_STATUSES and "cancelled" not in L.PURCHASE_STATUSES


def test_civil_som_is_uzs_and_lot_key_matches_results_rows():
    row = L.from_uzex("civil", CIVIL)
    assert row["currency"] == "UZS" and row["amount_uzs"] == "20000064", "«Сом» — это сум"
    assert row["status"] == "accepted"
    assert row["lot_key"] == "result-26120500017845", "так external_id «UZEX Результаты» в tenders"


def test_direct_division_is_category_not_subject():
    row = L.from_uzex("direct", DIRECT)
    assert row["category"] == "Услуги издательские" and row["subject"] is None
    assert L.needs_details(row), "печатный раздел — читаем позиции"
    other = L.from_uzex("direct", dict(DIRECT, category_name="Кокс и нефтепродукты"))
    assert not L.needs_details(other)


def test_foreign_currency_never_lands_in_uzs_sum():
    row = L.from_uzex("deals", dict(DEAL, currency_name="Доллар"))
    assert row["currency"] == "USD" and row["amount_uzs"] is None


def test_list_upsert_never_overwrites_card_columns():
    """Повторный прогон списка шлёт null в buyer_inn/subject — и стёр бы карточку."""
    item = {"procedure_id": "36099", "contract_number": "XD1", "lot_number": "L1", "buyer_name": "Buyer",
            "winner_name": "Winner", "amount": 18501910, "awarded_at": "2026-10-05 12:44:05",
            "raw": {"currency": "000"}}
    row = L.from_ebirja_list("shop", item)
    payload = L.list_payload(row)
    for column in ("buyer_inn", "winner_inn", "subject", "subject_codes", "subject_hash"):
        assert column not in payload, column
    assert payload["amount_uzs"] == "18501910" and payload["lot_key"] == "L1"
    direct = L.list_payload(L.from_uzex("direct", DIRECT))
    assert "subject" not in direct and direct["buyer_inn"] == "201000002"
    for name in ("profile", "entity_id", "miss_type", "details_fetched_at"):
        assert name not in payload and name not in direct, "колонки разбора список не трогает"


def test_all_list_payloads_of_a_feed_share_one_key_set():
    """PostgREST берёт колонки пачки из первого объекта — разнобой даёт null."""
    a = L.list_payload(L.from_uzex("deals", DEAL))
    b = L.list_payload(L.from_uzex("deals", dict(DEAL, deal_id=1, customer_inn=None, provider_inn="x")))
    assert set(a) == set(b)


def test_shop_card_gives_inn_subject_and_code():
    card = {"customer": {"tin": "200402830", "title": "Караозек районы"},
            "producer": {"tin": "312745119", "title": "QARAQALPAQ BUSINESS GROUP MCHJ"},
            "order": {"product_log": {"title": "17.23.13.110-00002-Jurnal ro'yxatga olish",
                                      "classifier": {"code": "17.23.13.110", "title_ru": "Журналы регистрации"}}}}
    details = L.ebirja_card_details("shop", card)
    assert details["buyer_inn"] == "200402830" and details["winner_inn"] == "312745119"
    assert details["subject_codes"] == ["17.23.13.110"]
    assert "Журналы регистрации" in details["subject"] and details["subject_hash"]
    row = {"feed": "ebirja_shop", "business_id": "36099"}
    payload = L.details_payload(row, details, "2026-10-07T00:00:00+00:00")
    assert payload["details_fetched_at"] and payload["buyer_inn"] == "200402830"


def test_tender_card_lists_every_lot_classifier():
    card = {"customer": {"tin": "203018986"}, "producer": {"tin": "306264592"},
            "tender": {"title": "Книги", "tender_classifiers": [
                {"classifier": {"code": "58.11.1", "title_ru": "Книги печатные"}},
                {"classifier": {"code": "58.11.1", "title_ru": "Книги печатные"}}]}}
    details = L.ebirja_card_details("selection", card)
    assert details["subject"] == "Книги печатные; Книги" and details["subject_codes"] == ["58.11.1"]


def test_direct_details_join_item_names():
    details = L.direct_details({"js_details": [{"product_name": "Бланки"}, {"product_name": "Бланки"},
                                               {"product_name": None, "description": "Журнал учёта"}]})
    assert details["subject"] == "Бланки; Журнал учёта"


class _Resp:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self.rows


def _pages(*pages):
    calls = []

    def post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        index = len(calls) - 1
        return _Resp(pages[index] if index < len(pages) else [])
    return post, calls


def test_backfill_stops_at_date_boundary_and_resumes_from_saved_page():
    B.RAW_DIR = Path(tempfile.mkdtemp())
    new = [dict(DEAL, deal_id=i, deal_date="2026-10-0%dT00:00:00" % (6 - i % 3)) for i in range(3)]
    old = [dict(DEAL, deal_id=10 + i, deal_date="2026-09-01T00:00:00") for i in range(2)]
    post, calls = _pages(new)
    first = B.collect_uzex("deals", date(2026, 9, 15), None, True, time.time() + 0.0, 0, False, post=post)
    assert first["completion"] == "time_budget" and calls == [], "бюджет кончился — ни одного запроса"
    post, calls = _pages(new, old)
    run = B.collect_uzex("deals", date(2026, 9, 15), None, True, time.time() + 30, 0, False, post=post)
    assert run["completion"] == "date_boundary" and run["complete"], run
    assert run["rows"] == 3, "строки старше границы в журнал не идут"
    assert calls[0]["From"] == 1 and calls[1]["From"] == 501, "DealsList: страницы с 1, To не включается"
    state = B._load_state("deals", "2026-09-15")
    assert state["complete"] and state["next_page"] == 2
    again = B.collect_uzex("deals", date(2026, 9, 15), None, True, time.time() + 30, 0, False, post=post)
    assert again["completion"] == "already_complete"


def test_interrupted_backfill_continues_where_it_stopped():
    B.RAW_DIR = Path(tempfile.mkdtemp())
    B._save_state("direct", {"since": "2025-07-01", "next_page": 7, "complete": False})
    post, calls = _pages([dict(DIRECT, id=1, contract_date="2025-06-01T00:00:00")])
    B.collect_uzex("direct", date(2025, 7, 1), None, True, time.time() + 30, 0, False, post=post)
    assert calls[0] == {"from": 1 + 7 * 500, "to": 8 * 500}, calls[0]


def test_api_error_is_reported_not_treated_as_archive_end():
    B.RAW_DIR = Path(tempfile.mkdtemp())

    def post(url, json=None, timeout=None, headers=None):
        raise RuntimeError("429 Too Many Requests")
    run = B.collect_uzex("civil", date(2026, 1, 1), None, True, time.time() + 30, 0, True, post=post)
    assert run["completion"].startswith("error") and not run["complete"]


def test_nul_from_api_is_scrubbed_before_it_reaches_postgres():
    """22P05: одна запись сделки с \u0000 (06.2025) уронила догрузку истории."""
    B.RAW_DIR = Path(tempfile.mkdtemp())
    seen = []

    class _T:
        def upsert(self, batch, on_conflict=None):
            seen.extend(batch)
            return self

        def execute(self):
            return None

    class _C:
        def table(self, name):
            return _T()
    post, calls = _pages([dict(DEAL, category_name="Bla\x00nki", customer_name="ASAKA\x00BANK")])
    B.collect_uzex("deals", date(2026, 9, 1), _C(), False, time.time() + 30, 0, True, post=post)
    assert seen and all("\x00" not in str(v) for row in seen for v in row.values())


def test_failed_page_write_stops_the_feed_without_moving_state():
    B.RAW_DIR = Path(tempfile.mkdtemp())

    class _T:
        def upsert(self, batch, on_conflict=None):
            return self

        def execute(self):
            raise RuntimeError("22P05 unsupported Unicode escape sequence")

    class _C:
        def table(self, name):
            return _T()
    B.time.sleep, sleep = (lambda s: None), B.time.sleep
    try:
        post, calls = _pages([DEAL])
        run = B.collect_uzex("deals", date(2026, 9, 1), _C(), False, time.time() + 30, 0, False, post=post)
    finally:
        B.time.sleep = sleep
    assert run["completion"].startswith("error: upsert") and not run["complete"]
    assert B._load_state("deals", "2026-09-01")["next_page"] == 0, "незаписанная страница будет повторена"


def test_upsert_drops_duplicate_keys_inside_one_batch():
    sent = []

    class _T:
        def upsert(self, batch, on_conflict=None):
            sent.append((batch, on_conflict))
            return self

        def execute(self):
            return None

    class _C:
        def table(self, name):
            return _T()
    rows = [{"feed": "deals", "business_id": "1", "amount": "1"}, {"feed": "deals", "business_id": "1", "amount": "2"}]
    assert B.upsert_rows(_C(), rows, False) == 1
    assert sent[0][1] == "feed,business_id" and sent[0][0][0]["amount"] == "2"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            print("FAIL", test.__name__, "-", repr(exc)[:200])
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
