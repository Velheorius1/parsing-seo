import sys

from crawler.scripts.enrich_ebirja_shop_candidates import candidate_rows, summarize_detail


def test_candidate_selection_covers_every_contract_type_and_uses_public_uzs_mapping():
    registry = {"entities": [{"name": "Kolorpak", "inn": "205353003", "input_names": ["kolorpak"]}],
                "separate_candidates": []}
    snapshot = {"sources": [
        {"source_key": "shop", "rows": [{"procedure_id": "7", "winner_name": "KOLORPAK MCHJ",
                                               "amount": 20000001, "raw": {"currency": "000"}}]},
        {"source_key": "auction", "rows": [{"procedure_id": "8", "winner_name": "KOLORPAK MCHJ",
                                                  "amount": 20000001, "raw": {"currency": "000"}}]},
    ]}
    rows = candidate_rows(snapshot, registry)
    assert [(r["source_key"], r["archive_row"]["procedure_id"]) for r in rows] == [("shop", "7"), ("auction", "8")]


def test_candidate_selection_skips_unknown_currency_and_small_or_unnamed_rows():
    registry = {"entities": [{"name": "Kolorpak", "inn": "205353003", "input_names": ["kolorpak"]}],
                "separate_candidates": []}
    snapshot = {"sources": [{"source_key": "selection", "rows": [
        {"procedure_id": "1", "winner_name": "KOLORPAK MCHJ", "amount": 90000000, "raw": {"currency": "840"}},
        {"procedure_id": "2", "winner_name": "KOLORPAK MCHJ", "amount": 1000, "raw": {"currency": "000"}},
        {"procedure_id": "3", "winner_name": "OTHER MCHJ", "amount": 90000000, "raw": {"currency": "000"}},
    ]}]}
    assert candidate_rows(snapshot, registry) == []


def test_auction_and_selection_cards_give_inn_subject_and_public_link():
    auction = summarize_detail({"id": 34944, "number": "XA26032279", "price": 343867216, "currency": "000",
                                "created_at": "2026-09-28 17:15:09",
                                "producer": {"title": "PREMIUM POLIGRAF BIZNES MCHJ", "tin": "303018986"},
                                "customer": {"title": "ASAKA AJ", "tin": "201589828"},
                                "auction": {"lot": "26521007040822", "auction_classifiers": [
                                    {"classifier": {"title_ru": "Книга кассира"}},
                                    {"classifier": {"title_ru": "Книга кассира"}}]}}, "auction")
    assert auction["winner_inn"] == "303018986" and auction["currency"] == "UZS"
    assert auction["title"] == "Книга кассира", "повтор позиции не дублируем"
    assert auction["source_url"] == "https://ebirja.uz/ru/contracts/auction/34944"
    assert auction["lot_number"] == "26521007040822" and auction["buyer_name"] == "ASAKA AJ"
    selection = summarize_detail({"id": 30771, "number": "XO26028396", "price": 631848000,
                                  "producer": {"title": "EDU PRESS MCHJ", "tin": "306264592"},
                                  "customer": {"title": "UNIVERSITETI"},
                                  "tender": {"lot": "26521012032424", "tender_classifiers": [
                                      {"classifier": {"title_ru": "Книги печатные"}}]}}, "selection")
    assert selection["title"] == "Книги печатные"
    assert selection["source_url"] == "https://ebirja.uz/ru/contracts/selection/30771"


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
