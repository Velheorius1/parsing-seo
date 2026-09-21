import sys

from crawler.scripts.enrich_ebirja_shop_candidates import candidate_rows, summarize_detail


def test_candidate_selection_is_shop_only_and_uses_public_uzs_mapping():
    registry = {"entities": [{"name": "Kolorpak", "inn": "205353003", "input_names": ["kolorpak"]}],
                "separate_candidates": []}
    snapshot = {"sources": [
        {"source_key": "shop", "rows": [{"procedure_id": "7", "winner_name": "KOLORPAK MCHJ",
                                               "amount": 20000001, "raw": {"currency": "000"}}]},
        {"source_key": "auction", "rows": [{"procedure_id": "8", "winner_name": "KOLORPAK MCHJ",
                                                  "amount": 20000001, "raw": {"currency": "000"}}]},
    ]}
    rows = candidate_rows(snapshot, registry)
    assert len(rows) == 1
    assert rows[0]["archive_row"]["procedure_id"] == "7"


def test_detail_summary_keeps_inn_and_hidden_print_specification():
    result = summarize_detail({"id": 7, "number": "XD7", "price": 25000000,
                               "producer": {"title": "CENTRIS-PRINT MCHJ", "tin": "308717019"},
                               "customer": {"title": "Buyer"},
                               "order": {"lot_number": "LOT7", "count": "10",
                                         "product_log": {"title": "Ручка", "description": "печать 4+4",
                                                         "classifier": {"code": "32", "title_ru": "Ручка"}}}})
    assert result["winner_inn"] == "308717019"
    assert result["currency"] == "UZS"
    assert result["description"] == "печать 4+4"


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
