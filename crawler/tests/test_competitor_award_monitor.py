import sys

from crawler.scripts.monitor_competitor_awards import (
    SOURCE_PASSPORT, build_digest, delta, deliver_report, multi_source_delta,
)


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


def test_all_exchange_report_keeps_unavailable_rows_and_preserves_their_state():
    prior = {"sources": {"uzex_direct": {"award_keys": ["uzex_direct:304788646:OLD"]}}}
    result = multi_source_delta({"etender_deals": {"status": "complete", "awards": [
        {"winner_inn": "304788646", "amount": 20000001, "currency": "UZS", "award_id": "A1"}]},
                                 "xt_xarid": {"status": "winner_unobservable"}}, prior)
    assert result["all_sources_reported"] is True
    assert len(result["sources"]) == len(SOURCE_PASSPORT)
    assert result["new_awards"][0]["key"] == "etender_deals:304788646:A1"
    statuses = {row["source_id"]: row["status"] for row in result["sources"]}
    assert statuses["xt_xarid"] == "winner_unobservable"
    assert statuses["hayotbirja"] == "not_collected"
    assert result["state_candidate"]["sources"]["uzex_direct"] == prior["sources"]["uzex_direct"]


def test_first_multi_source_run_is_a_silent_baseline():
    result = multi_source_delta({"etender_deals": {"status": "complete", "awards": [
        {"winner_inn": "304788646", "amount": 20000001, "currency": "UZS", "award_id": "A1"}]}}, {})
    assert result["bootstrap"] is True
    assert result["new_awards"] == []


def test_changed_confirmed_award_is_reported_without_becoming_new():
    run = {"uzex_direct": {"status": "complete", "captured_at": "2026-09-21T00:00:00Z", "awards": [{
        "winner_inn": "304788646", "contract_number": "77", "amount": "25000000", "currency": "UZS",
        "title": "old title"}]}}
    baseline = multi_source_delta(run, {"sources": {}})["state_candidate"]
    run["uzex_direct"]["awards"][0]["amount"] = "26000000"
    result = multi_source_delta(run, baseline)
    assert result["new_awards"] == []
    assert len(result["changed_awards"]) == 1


def test_bootstrap_and_empty_delta_do_not_call_telegram():
    calls = []
    sender = lambda text: calls.append(text) or True
    assert deliver_report({"bootstrap": True, "new_awards": [], "changed_awards": []}, sender) is True
    assert deliver_report({"bootstrap": False, "new_awards": [], "changed_awards": []}, sender) is True
    assert calls == []


def test_delivery_failure_is_reported_to_caller():
    report = {"bootstrap": False, "new_awards": [{"winner_name": "PRINTUZ", "winner_inn": "304788646",
              "amount": "25000001", "currency": "UZS", "title": "Книга"}], "changed_awards": []}
    assert deliver_report(report, lambda _text: False) is False
    assert "PRINTUZ" in build_digest(report)


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
