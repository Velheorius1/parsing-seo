"""C4: Ebirja Shop name candidates become wins only after exact INN proof."""
import sys
from datetime import date

from crawler.scripts import run_all_exchange_competitor_monitor as runner


REGISTRY = {
    "entities": [{"name": "Kolorpak", "inn": "205353003", "input_names": ["kolorpak"]}],
    "separate_candidates": [],
}


def test_detail_cards_require_exact_registry_inn_after_name_candidate_selection():
    details = [
        {"detail": {"winner_name": "KOLORPAK MCHJ", "winner_inn": "205049902", "contract_number": "wrong"}},
        {"detail": {"winner_name": "KOLORPAK MCHJ", "winner_inn": "205353003", "contract_number": "right"}},
    ]

    awards, rejected = runner._exact_ebirja_awards(details, REGISTRY)

    assert rejected == 1
    assert [row["contract_number"] for row in awards] == ["right"]
    assert awards[0]["competitor"] == "Kolorpak"


def test_wrong_detail_inn_does_not_make_full_detail_fetch_incomplete():
    original = (runner.collect_source, runner.candidate_rows, runner.enrich)
    try:
        runner.collect_source = lambda *_args: {"complete": True, "completion": "date_boundary"}
        runner.candidate_rows = lambda *_args: [{"archive_row": {"procedure_id": "1"}}]
        runner.enrich = lambda *_args: [{"detail": {"winner_inn": "205049902", "contract_number": "wrong"}}]
        result = runner._ebirja_run("shop", date(2026, 9, 1), 100, 1, REGISTRY, 1)
    finally:
        runner.collect_source, runner.candidate_rows, runner.enrich = original

    assert result["status"] == "complete"
    assert result["awards"] == []
    assert result["identity_rejected_count"] == 1


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
