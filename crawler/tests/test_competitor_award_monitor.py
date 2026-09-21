import sys

from crawler.scripts.monitor_competitor_awards import delta


def _snapshot(complete=True, fetched=1):
    return {"archive_complete": complete, "candidate_count": 1, "fetched_count": fetched,
            "captured_at": "2026-09-21T00:00:00Z", "results": [{"detail": {
                "winner_inn": "205353003", "winner_name": "KOLORPAK", "contract_number": "XD1",
                "procedure_id": "1", "amount": 20000001, "currency": "UZS", "product_title": "Книга"}}]}


def test_complete_snapshot_emits_only_unseen_exact_inn_award():
    result = delta(_snapshot(), {"award_keys": ["ebirja-shop:205353003:OLD"]})
    assert result["snapshot_complete"] is True
    assert [row["key"] for row in result["new_awards"]] == ["ebirja-shop:205353003:XD1"]


def test_incomplete_detail_snapshot_never_qualifies_for_state_advance():
    result = delta(_snapshot(fetched=0), {"award_keys": []})
    assert result["snapshot_complete"] is False
    assert len(result["new_awards"]) == 1


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
