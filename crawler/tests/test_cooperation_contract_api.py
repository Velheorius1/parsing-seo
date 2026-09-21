import sys

from crawler.scripts.collect_cooperation_contracts import normalize


def test_normalize_keeps_exact_inn_and_all_product_titles():
    registry = {"entities": [{"name": "Printuz", "inn": "123456789", "input_names": []}],
                "separate_candidates": []}
    row = normalize({"id": 7, "contractNumber": "AK7", "dealTime": "2026-09-21T12:00:00",
                     "producerTin": "123456789", "producerName": {"ru": "PRINTUZ MCHJ"},
                     "customerName": {"ru": "Buyer"}, "contractAmount": 25000000,
                     "products": [{"name": {"ru": "Бланки"}}, {"name": {"uz": "Jurnal"}}]}, registry)
    assert row["winner_inn"] == "123456789"
    assert row["competitor"] == "Printuz"
    assert row["title"] == "Бланки; Jurnal"
    assert row["currency"] is None


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
