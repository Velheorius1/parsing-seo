import sys

from crawler.scripts.collect_ebirja_contract_api import normalize


def test_normalize_keeps_public_winner_and_nested_tender_subject():
    row = normalize("tender", {"id": 7, "number": "XT1", "created_at": "2026-09-21 12:00:00",
                                "price": 25000000, "currency": "000", "producer": {"title": "PRINTUZ MCHJ"},
                                "customer": {"title": "Buyer"}, "tender": {"title": "Печать", "lot": "L1"}})
    assert row["winner_name"] == "PRINTUZ MCHJ"
    assert row["title"] == "Печать"
    assert row["amount"] == 25000000


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test(); print("PASS", test.__name__)
        except AssertionError as exc:
            print("FAIL", test.__name__, str(exc)); failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
