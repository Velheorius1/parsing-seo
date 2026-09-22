"""Durable handoff between detail fetch and database persistence."""
import asyncio
import json
import os
import sys
import tempfile
import types
from unittest.mock import AsyncMock, patch

if "crawler.config.settings" not in sys.modules:
    module = types.ModuleType("crawler.config.settings")
    module.settings = types.SimpleNamespace(
        supabase_url="", supabase_service_role_key="", telegram_bot_token="",
        telegram_alert_chat_id="", openrouter_api_key="", alert_keywords="",
        ai_score_threshold=70, ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = module

from crawler.adapters.api import ApiAdapter
from crawler.core import db
from crawler.core.models import RawTender, SourceConfig


def _config():
    return SourceConfig(
        id="detail-test", name="Detail Test", adapter="api", url="https://example.test",
        id_prefix="dt", detail_persistence=True,
        field_map={"title": "title", "organization": "organization", "external_id": "id"},
        keywords_fields=["title", "_detail_text"],
        detail_fetch={
            "url_template": "https://example.test/{id}", "state_key": "detail-test-state",
            "text_fields": ["details.*.name"],
        },
    )


class _Client:
    def __init__(self, fail_upsert=False):
        self.operation = None
        self.fail_upsert = fail_upsert

    def table(self, *_args):
        return self

    def select(self, *_args):
        self.operation = "select"
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def upsert(self, *_args, **_kwargs):
        self.operation = "upsert"
        return self

    def execute(self):
        if self.operation == "select":
            return types.SimpleNamespace(data=[])
        if self.fail_upsert:
            raise RuntimeError("simulated upsert failure")
        return types.SimpleNamespace(data=[])


def _tender():
    return RawTender(
        id="dt-42", external_id="42", title="Категория", organization="Заказчик",
        source="Detail Test", search_text="Категория",
        extra_info={"_detail_text": "Картонный картхолдер"}, detail_persistence=True,
    )


def test_cached_detail_is_restored_even_after_high_water_advanced():
    from crawler.core.detail_cache import store_detail
    from crawler.auth.session_store import session_store

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "details.json")
        with patch.dict(os.environ, {"PARSING_DETAIL_CACHE_PATH": cache_path}):
            assert store_detail("Detail Test", "42", "Картонный картхолдер")
            with patch.object(session_store, "get_setting", return_value={"max_seen_id": 42}):
                items = asyncio.run(ApiAdapter(_config())._enrich_with_details([{
                    "id": 42, "title": "Категория", "organization": "Заказчик",
                }]))

    assert items[0]["_detail_text"] == "Картонный картхолдер"


def test_successful_upsert_acknowledges_cached_detail():
    from crawler.core.detail_cache import read_details, store_detail

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "details.json")
        with patch.dict(os.environ, {"PARSING_DETAIL_CACHE_PATH": cache_path}):
            assert store_detail("Detail Test", "42", "Картонный картхолдер")
            settings = types.SimpleNamespace(
                supabase_url="fake", supabase_service_role_key="fake", batch_size=100,
            )
            with patch.object(db, "_get_client", return_value=_Client()), patch.object(db, "settings", settings):
                upserted, _new = asyncio.run(db.upsert_tenders([_tender()]))
            assert upserted == 1
            assert read_details() == {}


def test_failed_upsert_keeps_cached_detail_for_next_crawl():
    from crawler.core.detail_cache import read_details, store_detail

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "details.json")
        with patch.dict(os.environ, {"PARSING_DETAIL_CACHE_PATH": cache_path}):
            assert store_detail("Detail Test", "42", "Картонный картхолдер")
            settings = types.SimpleNamespace(
                supabase_url="fake", supabase_service_role_key="fake", batch_size=100,
            )
            with patch.object(db, "_get_client", return_value=_Client(fail_upsert=True)), patch.object(db, "settings", settings):
                upserted, _new = asyncio.run(db.upsert_tenders([_tender()]))
            assert upserted == 0
            assert len(read_details()) == 1


def test_cache_failure_does_not_advance_source_high_water():
    from crawler.auth.session_store import session_store

    class _Response:
        text = json.dumps({"details": [{"name": "Картонный картхолдер"}]})

        def raise_for_status(self):
            return None

    class _AsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url):
            return _Response()

    adapter = ApiAdapter(_config())
    adapter.rate_limit = AsyncMock()
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "details.json")
        with patch.dict(os.environ, {"PARSING_DETAIL_CACHE_PATH": cache_path}), \
                patch.object(session_store, "get_setting", return_value={"max_seen_id": 41}), \
                patch.object(session_store, "set_setting") as set_setting, \
                patch("crawler.adapters.api.httpx.AsyncClient", _AsyncClient), \
                patch("crawler.core.detail_cache.store_detail", return_value=False):
            items = asyncio.run(adapter._enrich_with_details([{
                "id": 42, "title": "Категория", "organization": "Заказчик",
            }]))

    assert not items[0].get("_detail_text")
    set_setting.assert_not_called()


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
