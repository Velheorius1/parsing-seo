"""ETender: спецификация для лотов, пришедших из «Обсуждений» (21.10.2026).

ЧТО БЫЛО. Деталь (budget_products — предмет закупки) качалась только для id выше
пола seen_floor. Лоты серии 2611… сначала проходят «Обсуждения» и входят в
активную выдачу TradeList позже — с номером ниже уже обработанных (518 749 при
пороге 520 709). За 30 дней деталь была у 1 из 1 337 таких строк, после #117 —
у 0 из 69: словарь и AI судили их по ОКЭД-категории из списка.

Здесь держится (active_list: список содержит только живые лоты): кандидат — любой
лот выдачи без детали, сколько бы ниже порога ни был его номер; лот с деталью из
кэша не качается заново; память обработанных обрезается по выдаче, а не по номеру;
у источников без флага поведение прежнее (пол).

Run: .venv/bin/python3 -m pytest crawler/tests/test_detail_active_list.py -q
"""
import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters import api as A  # noqa: E402
from crawler.auth.session_store import session_store  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE = {"max_seen_id": 521098, "seen_floor": 520709, "seen_ids": [520753, 521098]}


def _run(items, state, active_list=True, budget=250):
    fetched = []

    class _Resp(object):
        def __init__(self, iid):
            self.text = json.dumps({"details": [{"name": "печать брошюр %s" % iid}]})

        def raise_for_status(self):
            return None

    class _Client(object):
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url):
            iid = int(url.rsplit("/", 1)[1])
            fetched.append(iid)
            return _Resp(iid)

    cfg = SourceConfig(
        id="etender-test", name="ETender Test", adapter="api", url="https://example.test",
        id_prefix="et", field_map={"title": "title", "external_id": "id"},
        keywords_fields=["title", "_detail_text"],
        detail_fetch={"url_template": "https://example.test/{id}", "state_key": "et-state",
                      "text_fields": ["details.*.name"], "max_per_run": budget, "retries": 0,
                      "active_list": active_list},
    )
    adapter = A.ApiAdapter(cfg)
    adapter.rate_limit = AsyncMock()
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"PARSING_DETAIL_CACHE_PATH": os.path.join(tmp, "c.json")}), \
                patch.object(session_store, "get_setting", return_value=state), \
                patch.object(session_store, "set_setting") as put, \
                patch("crawler.adapters.api.httpx.AsyncClient", _Client):
            out = asyncio.run(adapter._enrich_with_details(items))
    return fetched, (put.call_args[0][1] if put.called else None), out


def _items(*ids):
    return [{"id": i, "title": "ОКЭД"} for i in ids]


def test_lot_from_discussions_below_the_floor_gets_its_detail():
    fetched, state, out = _run(_items(518749, 520753, 521098, 521100), dict(STATE))
    assert fetched == [518749, 521100]
    assert {it["id"]: "_detail_text" in it for it in out}[518749]
    assert 518749 in state["seen_ids"]


def test_lot_with_cached_detail_is_not_fetched_again():
    items = _items(518749, 521100)
    items[0]["_detail_text"] = "уже есть"
    fetched, _, _ = _run(items, dict(STATE))
    assert fetched == [521100]


def test_memory_is_pruned_to_the_live_list():
    """Ушедший из выдачи лот не возвращается: 520753 и 521098 больше не в списке."""
    _, state, _ = _run(_items(518749), dict(STATE))
    assert state["seen_ids"] == [518749]


def test_budget_still_caps_the_backfill():
    fetched, _, _ = _run(_items(*range(500000, 500010)), {}, budget=4)
    assert len(fetched) == 4


def test_sources_without_the_flag_keep_the_floor():
    fetched, _, _ = _run(_items(518749, 521100), dict(STATE), active_list=False)
    assert fetched == [521100]


def test_etender_is_configured_as_an_active_list():
    cfg = yaml.safe_load(open(os.path.join(_ROOT, "crawler/config/sources.yaml"), encoding="utf-8"))
    et = [s for s in cfg["sources"] if s["id"] == "etender"][0]
    assert et["detail_fetch"]["active_list"] is True
