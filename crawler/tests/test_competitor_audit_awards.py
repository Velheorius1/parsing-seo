"""Нормализация результатов: статусы и суммы нельзя угадывать по заголовку."""
import sys

from crawler.core.competitor_audit import award_in_window, normalize_award


def test_accepted_deal_has_final_amount_and_trade_evidence_url():
    award = normalize_award("deals", {
        "deal_id": 174629, "trade_id": 507826, "provider_inn": "308044785",
        "customer_inn": "201453166", "category_name": "chop etish",
        "deal_cost": 23520000, "currency_name": "Сум",
        "deal_date": "2026-09-02T12:38:10",
        "deal_status_name": "Ғолиб томонидан қабул қилинган (Амалга ошган)",
    })

    assert award["status"] == "accepted"
    assert award["final_total"] == "23520000"
    assert award["currency"] == "UZS"
    assert award["procedure_type"] == "etender_deal"
    assert award["evidence_url"] == "https://etender.uzex.uz/lot/507826"


def test_rejected_deal_is_not_normalized_as_win():
    award = normalize_award("deals", {
        "deal_id": 9, "trade_id": 10, "provider_inn": "304788646",
        "deal_cost": 50000000, "currency_name": "Сум",
        "deal_status_name": "Заказчиком отклонен",
    })

    assert award["status"] == "rejected"
    assert award["is_win"] is False


def test_direct_contract_is_not_open_competition():
    award = normalize_award("direct", {
        "id": 4418905, "provider_inn": "204447012", "customer_inn": "202085609",
        "category_name": "Изделия резиновые и пластмассовые", "contract_sum": 98590000,
        "currency_name": "UZS", "contract_date": "2026-06-08T00:00:00",
        "status_name": "Опубликован",
    })

    assert award["status"] == "contract_published"
    assert award["is_open_competition"] is False
    assert award["final_total"] == "98590000"
    assert award["evidence_url"].endswith("/4418905")


def test_unknown_amount_is_not_replaced_with_start_price():
    award = normalize_award("deals", {
        "deal_id": 9, "trade_id": 10, "provider_inn": "304788646",
        "start_cost": 50000000, "currency_name": "Сум", "deal_status_name": "Протокол сформирован",
    })

    assert award["final_total"] is None
    assert award["above_threshold"] is None
    assert award["start_total"] == "50000000"


def test_award_window_uses_awarded_date_and_preserves_unknown():
    award = normalize_award("deals", {
        "deal_id": 9, "trade_id": 10, "provider_inn": "304788646",
        "deal_cost": 50000000, "currency_name": "Сум", "deal_date": "2026-09-13T12:00:00",
    })
    assert award_in_window(award, "2025-09-13", "2026-09-13") is True
    assert award_in_window(award, "2026-09-14", "2026-09-21") is False
    assert award_in_window({"awarded_at": None}, "2025-09-13", "2026-09-13") is None


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except (AssertionError, ImportError) as exc:
            print("FAIL", test.__name__, "-", str(exc)[:140])
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
