"""Pure contracts for bounded detail-gap recovery."""
import sys
import json
import tempfile
import types
from pathlib import Path

if "crawler.config.settings" not in sys.modules:
    module = types.ModuleType("crawler.config.settings")
    module.settings = types.SimpleNamespace(
        supabase_url="", supabase_service_role_key="", telegram_bot_token="",
        telegram_alert_chat_id="", openrouter_api_key="", alert_keywords="",
        ai_score_threshold=70, ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = module

from crawler.core.models import SourceConfig
from crawler.scripts.recover_detail_gap import (
    candidate_ids, detail_id_for_row, detail_text, load_targets, parse_gap,
    update_payload,
)


def _config():
    return SourceConfig(
        id="etender", name="ETender UZEX", adapter="api", url="https://example.test",
        id_prefix="etender", detail_persistence=True,
        detail_fetch={
            "url_template": "https://example.test/{id}", "state_key": "detail-test",
            "text_fields": ["budget_products.*.Product_Name", "budget_products.*.Description"],
            "json_string_fields": ["budget_products"],
        },
    )


def test_gap_is_exclusive_then_inclusive():
    assert parse_gap("ETender UZEX:10:13") == ("ETender UZEX", 10, 13)
    assert candidate_ids(10, 13) == ["11", "12", "13"]


def test_json_string_detail_is_parsed_with_configured_fields():
    payload = {"budget_products": '[{"Product_Name":"Картхолдер","Description":"картон"}]'}
    assert detail_text(payload, _config()) == "Картхолдер картон"


def test_recovery_update_preserves_metadata_and_prepends_detail_once():
    row = {"search_text": "Услуги профессиональные", "extra_info": {"buyer": "Банк"}}
    payload = update_payload(row, "Картхолдер картонный", "2026-09-22T00:00:00Z")
    assert payload["search_text"].startswith("Картхолдер картонный")
    assert payload["extra_info"]["buyer"] == "Банк"
    assert payload["extra_info"]["_detail_text"] == "Картхолдер картонный"


def test_detail_id_uses_source_url_when_external_id_is_a_display_number():
    row = {
        "external_id": "26111006513690",
        "source_url": "https://etender.uzex.uz/lot/513690",
    }
    assert detail_id_for_row(row, ["513690", "513708"]) == "513690"


def test_detail_id_prefers_exact_external_id_for_xarid():
    row = {
        "external_id": "24542",
        "source_url": "https://xarid.uzex.uz/competition/24542",
    }
    assert detail_id_for_row(row, ["24542"]) == "24542"


def test_exact_target_manifest_is_deduplicated_and_validated():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "targets.json"
        path.write_text(json.dumps({"targets": [
            {"source": "ETender UZEX", "detail_id": "513690"},
            {"source": "ETender UZEX", "detail_id": "513690"},
            {"source": "Xarid Конкурсы", "detail_id": 24542},
        ]}), encoding="utf-8")
        assert load_targets(str(path)) == [
            ("ETender UZEX", "513690"),
            ("Xarid Конкурсы", "24542"),
        ]


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            print("FAIL", test.__name__, "%s: %s" % (type(exc).__name__, exc))
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    raise SystemExit(1 if failures else 0)
