"""C2 contract: repeat list crawls must not erase fetched specifications."""
import sys
import types
import os
import asyncio
from unittest.mock import patch

import yaml

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        supabase_url="", supabase_service_role_key="", telegram_bot_token="",
        telegram_alert_chat_id="", openrouter_api_key="", alert_keywords="",
        ai_score_threshold=70, ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core import db
from crawler.core.db import _restore_persisted_detail, upsert_tenders
from crawler.core.models import RawTender
from crawler.core.notifier import _format_alert
from crawler.adapters.api import ApiAdapter
from crawler.core.models import SourceConfig


def _tender(**changes):
    data = {
        "id": "t-1", "external_id": "1", "title": "Услуги профессиональные",
        "organization": "Заказчик", "source": "ETender UZEX",
        "search_text": "Услуги профессиональные Заказчик",
    }
    data.update(changes)
    return RawTender(**data)


def test_repeat_list_upsert_restores_hidden_detail_and_does_not_alert_it():
    tender = _tender(detail_persistence=True)
    rows = {("1", "ETender UZEX"): {
        "extra_info": {"_detail_text": "Картхолдер картонный с печатью 1000 шт"}
    }}

    _restore_persisted_detail([tender], rows)

    assert tender.search_text.startswith("Картхолдер картонный"), tender.search_text
    assert "Услуги профессиональные" in tender.search_text
    assert tender.extra_info["_detail_text"].startswith("Картхолдер")
    assert "Картхолдер картонный" not in _format_alert(tender, "печать")


def test_fresh_detail_replaces_stored_detail_instead_of_being_overwritten():
    tender = _tender(
        detail_persistence=True,
        search_text="Категория НОВЫЙ КАРТОННЫЙ КАРТХОЛДЕР",
        extra_info={"_detail_text": "НОВЫЙ КАРТОННЫЙ КАРТХОЛДЕР"},
    )
    rows = {("1", "ETender UZEX"): {
        "extra_info": {"_detail_text": "СТАРАЯ КОЖАНАЯ ПАПКА"},
        "search_text": "Категория СТАРАЯ КОЖАНАЯ ПАПКА",
    }}

    _restore_persisted_detail([tender], rows)

    assert tender.extra_info["_detail_text"] == "НОВЫЙ КАРТОННЫЙ КАРТХОЛДЕР"
    assert tender.search_text.startswith("Категория НОВЫЙ"), tender.search_text
    assert "СТАРАЯ КОЖАНАЯ" not in tender.search_text


def test_legacy_search_text_detail_is_migrated_only_when_list_text_is_a_prefix():
    tender = _tender(detail_persistence=True, search_text="Категория Заказчик")
    rows = {("1", "ETender UZEX"): {
        "extra_info": {},
        "search_text": "Категория Заказчик | КАРТОННЫЙ КАРТХОЛДЕР С ПЕЧАТЬЮ",
    }}

    _restore_persisted_detail([tender], rows)

    assert tender.extra_info["_detail_text"] == "КАРТОННЫЙ КАРТХОЛДЕР С ПЕЧАТЬЮ"
    assert tender.search_text.startswith("КАРТОННЫЙ КАРТХОЛДЕР"), tender.search_text


def test_prequal_lots_survive_live_list_metadata_and_restore_subject():
    tender = _tender(
        source="UZEX Предквалификации", detail_persistence=True,
        extra_info={"customer_inn": "123456789", "display_id": "PRQ-1"},
    )
    rows = {("1", "UZEX Предквалификации"): {
        "extra_info": {"lots": [{"productName": "Печать буклетов", "description": "мелованная бумага"}]}
    }}

    _restore_persisted_detail([tender], rows)

    assert "Печать буклетов" in tender.search_text
    assert tender.extra_info["customer_inn"] == "123456789"
    assert tender.extra_info["lots"][0]["description"] == "мелованная бумага"


def test_fresh_prequal_lots_are_not_replaced_by_stored_lots():
    tender = _tender(
        source="UZEX Предквалификации", detail_persistence=True,
        extra_info={"lots": [{"productName": "Новая позиция", "description": "картон"}]},
    )
    rows = {("1", "UZEX Предквалификации"): {
        "extra_info": {"lots": [{"productName": "Старая позиция", "description": "кожа"}]},
    }}

    _restore_persisted_detail([tender], rows)

    assert tender.extra_info["lots"][0]["productName"] == "Новая позиция"
    assert "Новая позиция" in tender.search_text
    assert "Старая позиция" not in tender.search_text


class _LookupFailureClient:
    def __init__(self):
        self.operation = None
        self.upsert_rows = []

    def table(self, *_args):
        return self

    def select(self, *_args):
        self.operation = "select"
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def upsert(self, rows, **_kwargs):
        self.operation = "upsert"
        self.upsert_rows.extend(rows)
        return self

    def execute(self):
        if self.operation == "select":
            raise RuntimeError("simulated lookup timeout")
        return types.SimpleNamespace(data=[])


def test_lookup_failure_defers_tender_without_write_or_false_new_alert():
    client = _LookupFailureClient()
    settings = types.SimpleNamespace(
        supabase_url="fake", supabase_service_role_key="fake", batch_size=100,
    )
    with patch.object(db, "_get_client", return_value=client), patch.object(db, "settings", settings):
        upserted, new_tenders = asyncio.run(upsert_tenders([_tender(detail_persistence=True)]))

    assert upserted == 0
    assert new_tenders == []
    assert client.upsert_rows == []


def test_source_without_opt_in_is_not_changed():
    tender = _tender(detail_persistence=False)
    before = (tender.search_text, dict(tender.extra_info))

    _restore_persisted_detail([tender], {
        ("1", "ETender UZEX"): {"extra_info": {"_detail_text": "Печать календарей"}}
    })

    assert (tender.search_text, tender.extra_info) == before


def test_api_adapter_marks_and_persists_first_fetched_detail():
    config = SourceConfig(
        id="detail-test", name="Detail test", adapter="api", url="https://example.test",
        id_prefix="dt", detail_persistence=True,
        field_map={"title": "title", "organization": "organization", "external_id": "id"},
        keywords_fields=["title", "_detail_text"],
    )

    tender = ApiAdapter(config)._convert_item({
        "id": "42", "title": "Категория", "organization": "Заказчик",
        "_detail_text": "Картхолдер картонный",
    })

    assert tender is not None
    assert tender.detail_persistence is True
    assert tender.extra_info["_detail_text"] == "Картхолдер картонный"
    assert "Картхолдер картонный" in tender.search_text


def test_only_detail_capable_sources_opt_in_in_config():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "crawler/config/sources.yaml"), encoding="utf-8") as handle:
        sources = yaml.safe_load(handle)["sources"]
    opted_in = {s["name"] for s in sources if s.get("detail_persistence")}
    assert opted_in == {
        "ETender UZEX", "Xarid Конкурсы", "UZEX Предквалификации",
    }, opted_in


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    raise SystemExit(1 if failures else 0)
