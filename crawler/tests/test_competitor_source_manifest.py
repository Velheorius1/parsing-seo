import sys

from crawler.scripts.build_competitor_source_manifest import build


def test_manifest_contains_every_exchange_and_never_converts_limits_to_zero():
    ebirja = {"archive_complete": True, "candidate_count": 1, "fetched_count": 1,
              "results": [{"detail": {"winner_inn": "205353003", "amount": 30000000,
                                      "currency": "UZS", "contract_number": "XD1"}}]}
    result = build([{"is_win": True, "above_threshold": True, "winner_inn": "304788646",
                     "final_total": 30000000, "currency": "UZS", "award_id": "A1"}], [], ebirja)
    assert len(result) == 9
    assert result["etender_deals"]["status"] == "complete"
    assert result["etender_deals"]["awards"][0]["is_win"] is True
    assert result["ebirja_shop"]["awards"][0]["winner_inn"] == "205353003"
    assert result["cooperation_contracts"]["status"] == "currency_unobservable"
    assert result["xt_xarid"]["status"] == "winner_unobservable"
    assert result["hayotbirja"]["status"] == "mirror"


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
