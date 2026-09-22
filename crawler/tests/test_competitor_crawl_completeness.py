"""C3: bad or partial public responses must never look like a complete sweep."""
import sys
from datetime import date

from crawler.scripts.collect_ebirja_contract_api import collect_source
from crawler.scripts.collect_uzex_award_api import collect
from crawler.scripts import run_all_exchange_competitor_monitor as runner
from crawler.scripts.monitor_competitor_awards import multi_source_delta


class _Response:
    content = b"{}"

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self, payload, **_kwargs):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        return _Response(self.payload)


def test_uzex_http_200_object_is_incomplete_not_empty_archive():
    result = collect("deals", date(2026, 9, 1), 100, 1,
                     post=lambda *_args, **_kwargs: _Response({"error": "temporary"}))
    assert result["complete"] is False
    assert result["completion"] == "invalid_payload_schema"
    assert result["receipts"][0]["rows"] is None


def test_ebirja_missing_page_count_is_incomplete_even_with_rows():
    payload = {"result": {"data": [{"id": 1, "created_at": "2026-09-21 00:00:00"}],
                          "meta": {}}}
    result = collect_source("auction", date(2026, 9, 1), 100, 1,
                            client_factory=lambda **kwargs: _Client(payload, **kwargs))
    assert result["complete"] is False
    assert result["completion"] == "invalid_page_count"


def test_ebirja_bad_rows_schema_is_incomplete_not_empty_archive():
    result = collect_source("auction", date(2026, 9, 1), 100, 1,
                            client_factory=lambda **kwargs: _Client({"result": {"meta": {}}}, **kwargs))
    assert result["complete"] is False
    assert result["completion"] == "invalid_rows_schema"


def test_name_only_source_keeps_incomplete_status_from_its_receipt():
    original = runner.collect_source
    try:
        runner.collect_source = lambda *_args, **_kwargs: {"complete": False, "completion": "page_cap"}
        result = runner._ebirja_run("auction", date(2026, 9, 1), 100, 1, {}, 1)
    finally:
        runner.collect_source = original
    assert result["status"] == "incomplete_name_only"
    assert result["detail"].endswith("page_cap")


def test_passport_with_unrun_or_incomplete_source_is_not_all_reported():
    prior = {"sources": {"uzex_direct": {"award_keys": ["uzex_direct:304788646:old"]}}}
    result = multi_source_delta({
        "etender_deals": {"status": "complete", "awards": []},
        "uzex_direct": {"status": "incomplete"},
        "ebirja_auction": {"status": "incomplete_name_only"},
        "xt_xarid": {"status": "winner_unobservable"},
        "hayotbirja": {"status": "mirror"},
    }, prior)
    assert result["all_sources_reported"] is False
    assert result["state_candidate"]["sources"]["uzex_direct"] == prior["sources"]["uzex_direct"]


def test_all_nine_complete_or_known_limit_rows_are_reported():
    result = multi_source_delta({
        "etender_deals": {"status": "complete", "awards": []},
        "uzex_direct": {"status": "complete", "awards": []},
        "ebirja_shop": {"status": "complete", "awards": []},
        "ebirja_auction": {"status": "complete_name_only"},
        "ebirja_tender": {"status": "complete_name_only"},
        "ebirja_selection": {"status": "complete_name_only"},
        "cooperation_contracts": {"status": "currency_unobservable"},
        "xt_xarid": {"status": "winner_unobservable"},
        "hayotbirja": {"status": "mirror"},
    }, {"sources": {}})
    assert result["all_sources_reported"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test(); print("PASS", test.__name__)
        except (AssertionError, ImportError, ValueError) as exc:
            print("FAIL", test.__name__, "-", str(exc)[:160]); failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
