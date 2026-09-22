import sys
from datetime import date

from crawler.scripts.enrich_competitor_award_specs import (
    enrich_awards, summarize_direct_detail, summarize_etender_detail,
)
from crawler.scripts import run_all_exchange_competitor_monitor as runner


class _Response:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


def test_etender_budget_products_becomes_compact_specification():
    payload = {"budget_products": '[{"Product_Name": "Laminatsiyalangan yo\'riqnoma", "Quantity": 50000, '
                                  '"Description": "офсетная печать, ламинация"}]'}
    assert summarize_etender_detail(payload) == "Laminatsiyalangan yo'riqnoma · 50000; офсетная печать, ламинация"


def test_direct_js_details_becomes_compact_specification():
    payload = {"js_details": [{"product_name": "Полиграфические услуги", "quantity": 1,
                                "description": "бланки офсет"}]}
    assert summarize_direct_detail(payload) == "Полиграфические услуги · 1; бланки офсет"


def test_malformed_detail_is_optional_not_an_error():
    assert summarize_etender_detail({"budget_products": "not-json"}) is None
    assert summarize_direct_detail({"js_details": "not-a-list"}) is None


def test_enrichment_fetches_only_confirmed_qualified_rows_and_isolates_failures():
    calls = []

    def get(url, timeout):
        calls.append(url)
        if url.endswith("/bad/0"):
            return _Response(error=RuntimeError("temporary detail failure"))
        return _Response({"budget_products": '[{"Product_Name":"Бланки", "Quantity":100000}]'})

    awards = [
        {"procedure_id": "good", "is_win": True, "above_threshold": True, "currency": "UZS"},
        {"procedure_id": "bad", "is_win": True, "above_threshold": True, "currency": "UZS"},
        {"procedure_id": "not-winner", "is_win": False, "above_threshold": True, "currency": "UZS"},
        {"procedure_id": "below", "is_win": True, "above_threshold": False, "currency": "UZS"},
        {"procedure_id": "currency", "is_win": True, "above_threshold": True, "currency": "USD"},
    ]
    result, summary = enrich_awards("etender_deals", awards, get, max_details=25)
    assert [url.rsplit("/", 2)[-2] for url in calls] == ["good", "bad"]
    assert result[0]["specification_text"] == "Бланки · 100000"
    assert "specification_text" not in result[1]
    assert summary == {"attempted": 2, "enriched": 1, "failed": 1, "capped": 0}


def test_enrichment_honors_cap_without_touching_later_awards():
    calls = []

    def get(url, timeout):
        calls.append(url)
        return _Response({"budget_products": '[{"Product_Name":"Книга"}]'})

    awards = [{"procedure_id": str(index), "is_win": True, "above_threshold": True, "currency": "UZS"}
              for index in range(3)]
    result, summary = enrich_awards("etender_deals", awards, get, max_details=2)
    assert len(calls) == 2
    assert [row.get("specification_text") for row in result] == ["Книга", "Книга", None]
    assert summary == {"attempted": 2, "enriched": 2, "failed": 0, "capped": 1}


def test_all_exchange_runner_attaches_detail_receipt_without_changing_passport():
    original = (runner.collect_uzex, runner.enrich_awards, runner.load_registry,
                runner._ebirja_run, runner.collect_cooperation)
    calls = []
    try:
        runner.collect_uzex = lambda key, *_args: {"complete": True, "captured_at": "now",
                                                     "awards": [{"procedure_id": key}],
                                                     "completion": "short_page"}
        runner.enrich_awards = lambda source_id, awards, _get, max_details: (
            [dict(award, specification_text="позиция " + source_id) for award in awards],
            {"attempted": 1, "enriched": 1, "failed": 0, "capped": 0})
        runner.load_registry = lambda: {}
        runner._ebirja_run = lambda source_key, *_args: {"status": "complete_name_only",
                                                           "detail": source_key}
        runner.collect_cooperation = lambda *_args: {"ok": True}
        result = runner.build_runs(date(2026, 9, 1), 100, 1)
        assert len(result) == 9
        for source_id in ("etender_deals", "uzex_direct"):
            assert result[source_id]["awards"][0]["specification_text"] == "позиция " + source_id
            assert result[source_id]["detail_enrichment"]["enriched"] == 1
    finally:
        (runner.collect_uzex, runner.enrich_awards, runner.load_registry,
         runner._ebirja_run, runner.collect_cooperation) = original


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
