"""Pin publicly visible Ebirja contract card parsing for audit use."""
import sys

from crawler.scripts.collect_ebirja_contracts import _contract_date, _is_complete
from crawler.scripts.fetch_ebirja_contracts import _parse_contract_text


def test_parser_keeps_winner_as_a_structured_field():
    row = _parse_contract_text("""№ 26521006031792
18.09.2026
Договор № XT26030386
Заказчик:
Mikrokreditbank Qashqadaryo BXO
Цена победителя:
14 545 353 748.8 UZS
Исполнитель:
PRINTUZ MCHJ
Статус:
Принял""", "https://ebirja.uz/ru/contracts/tender/32763", "tender")
    assert row["winner_name"] == "PRINTUZ MCHJ"
    assert row["price"] == 14545353748.8
    assert row["source_url"].endswith("/32763")


def test_contract_date_uses_public_card_day_month_year_format():
    assert str(_contract_date({"deadline": "18.09.2026"})) == "2026-09-18"


def test_missing_paginator_cannot_certify_annual_archive_complete():
    assert _is_complete([{"completion": "date_boundary"}, {"completion": "empty_page"}])
    assert not _is_complete([{"completion": "no_next_page"}])
    assert not _is_complete([{"completion": "page_cap"}])


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except (AssertionError, ImportError, TypeError) as exc:
            print("FAIL", test.__name__, "-", str(exc)[:140])
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
