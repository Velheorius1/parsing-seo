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
from crawler.core.db import _get_existing_rows, _restore_persisted_detail, upsert_tenders
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
        outcome = asyncio.run(upsert_tenders([_tender(detail_persistence=True)]))

    assert outcome.total_upserted == 0
    assert outcome.new_tenders == []
    assert outcome.deferred_count == 1
    assert client.upsert_rows == []


def test_failed_upsert_is_not_returned_as_a_new_alert_candidate():
    client = _MemoryClient()
    client.execute = lambda: (_ for _ in ()).throw(RuntimeError("upsert unavailable")) \
        if client.operation == "upsert" else types.SimpleNamespace(data=[])

    with patch("time.sleep", return_value=None):
        outcome = _upsert_with_client([_tender()], client)

    assert outcome.total_upserted == 0
    assert outcome.new_tenders == []
    assert outcome.failed_count == 1


class _ChunkedLookupClient:
    def __init__(self, fail_first=False, fail_prefix=None):
        self.fail_first = fail_first
        self.fail_prefix = fail_prefix
        self.calls = []
        self.current_ids = []

    def table(self, *_args):
        return self

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def in_(self, _field, values):
        self.current_ids = list(values)
        return self

    def execute(self):
        ids = list(self.current_ids)
        self.calls.append(ids)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("502 Bad Gateway")
        if self.fail_prefix and any(value.startswith(self.fail_prefix) for value in ids):
            raise RuntimeError("502 Bad Gateway")
        return types.SimpleNamespace(data=[])


def test_existing_lookup_chunks_long_id_lists_below_url_limit():
    tenders = [
        _tender(id="t-%d" % i, external_id="2611100651%04d" % i)
        for i in range(205)
    ]
    client = _ChunkedLookupClient()

    existing, unknown = _get_existing_rows(client, tenders)

    assert existing == {}
    assert unknown == set()
    assert [len(batch) for batch in client.calls] == [40, 40, 40, 40, 40, 5]


def test_existing_lookup_retries_a_transient_gateway_failure():
    tenders = [_tender(external_id="26111006510001")]
    client = _ChunkedLookupClient(fail_first=True)

    with patch("time.sleep", return_value=None):
        existing, unknown = _get_existing_rows(client, tenders)

    assert existing == {}
    assert unknown == set()
    assert len(client.calls) == 2


def test_existing_lookup_marks_only_the_persistently_failed_chunk_unknown():
    tenders = [
        _tender(id="t-%d" % i, external_id=("bad-" if i < 80 else "ok-") + str(i))
        for i in range(150)
    ]
    client = _ChunkedLookupClient(fail_prefix="bad-")

    with patch("time.sleep", return_value=None):
        existing, unknown = _get_existing_rows(client, tenders)

    assert existing == {}
    assert len(unknown) == 80
    assert all(external_id.startswith("bad-") for external_id, _source in unknown)
    assert max(len(batch) for batch in client.calls) == 40


class _MemoryClient:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.operation = None
        self.source = None
        self.ids = []
        self.upsert_rows = []

    def table(self, *_args):
        return self

    def select(self, *_args):
        self.operation = "select"
        return self

    def eq(self, field, value):
        if field == "source":
            self.source = value
        return self

    def in_(self, field, values):
        if field == "external_id":
            self.ids = list(values)
        return self

    def upsert(self, rows, **_kwargs):
        self.operation = "upsert"
        self.upsert_rows.extend(rows)
        return self

    def execute(self):
        if self.operation == "select":
            rows = [row for row in self.existing
                    if row.get("source") == self.source and row.get("external_id") in self.ids]
            return types.SimpleNamespace(data=rows)
        return types.SimpleNamespace(data=[])


def _upsert_with_client(tenders, client):
    settings = types.SimpleNamespace(
        supabase_url="fake", supabase_service_role_key="fake", batch_size=100,
    )
    with patch.object(db, "_get_client", return_value=client), patch.object(db, "settings", settings):
        return asyncio.run(upsert_tenders(tenders))


def test_new_detail_capable_list_tender_without_detail_is_written_as_new():
    client = _MemoryClient()
    tender = _tender(detail_persistence=True)

    upserted, new_tenders = _upsert_with_client([tender], client)

    assert upserted == 1
    assert new_tenders == [tender]
    assert len(client.upsert_rows) == 1
    assert client.upsert_rows[0]["external_id"] == "1"


def test_new_detail_capable_tender_with_fresh_detail_is_written_as_new():
    client = _MemoryClient()
    tender = _tender(
        detail_persistence=True,
        extra_info={"_detail_text": "Картонный картхолдер"},
    )

    upserted, new_tenders = _upsert_with_client([tender], client)

    assert upserted == 1
    assert new_tenders == [tender]
    assert client.upsert_rows[0]["extra_info"]["_detail_text"] == "Картонный картхолдер"


def test_new_prequalification_with_fresh_lots_is_written_as_new():
    client = _MemoryClient()
    tender = _tender(
        source="UZEX Предквалификации",
        detail_persistence=True,
        extra_info={"lots": [{"productName": "Печать буклетов", "description": "картон"}]},
    )

    upserted, new_tenders = _upsert_with_client([tender], client)

    assert upserted == 1
    assert new_tenders == [tender]
    assert "Печать буклетов" in client.upsert_rows[0]["search_text"]


def test_mixed_new_and_existing_sources_are_written_without_false_new_rows():
    client = _MemoryClient(existing=[{
        "external_id": "2", "source": "Ordinary Source",
    }])
    new_detail_capable = _tender(detail_persistence=True)
    existing_ordinary = _tender(
        id="t-2", external_id="2", source="Ordinary Source", detail_persistence=False,
    )

    upserted, new_tenders = _upsert_with_client(
        [new_detail_capable, existing_ordinary], client,
    )

    assert upserted == 2
    assert new_tenders == [new_detail_capable]
    assert {row["external_id"] for row in client.upsert_rows} == {"1", "2"}


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
