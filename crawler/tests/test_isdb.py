"""IsDB снова собирается, у каждого тендера свой id и читаемый срок (10.10.2026).

ЧТО БЫЛО. Источник дал за всю историю 2 строки (последняя 03.03) и с тех пор
каждый прогон «Fetched 0» без ошибки. Три поломки разом:
  1. без параметров страница отдаёт «No results for your specified filters» —
     сервер рендерит список только с фильтром формы (?loc=UZ&status=active);
  2. id брался как «первое число в ссылке», а ссылка /tenders/2025/gpn/<slug> —
     id становился ГОДОМ, и все тендеры года ложились в одну строку (в БД
     лежали id «2024» и «2025»): новый тендер не был бы «новым» и не алертился;
  3. срок «20 October 2026» разборщик не читал — закрытые лоты сентября 2025
     считались бы живыми.

Run: .venv/bin/python3 -m pytest crawler/tests/test_isdb.py -q
"""
import asyncio
import os
import sys
import types
from datetime import datetime

import httpx
import yaml

if "pydantic_settings" not in sys.modules:
    _stub = types.ModuleType("pydantic_settings")

    class _BaseSettings(object):
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    _stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = _stub

import crawler.adapters.html as html_mod  # noqa: E402
from crawler.adapters.html import HtmlAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
UZ_ACTIVE = open(os.path.join(_HERE, "fixtures", "isdb_uz_active_2026-10-10.html"),
                 encoding="utf-8").read()
NO_RESULTS = "<html><body><article>No results for your specified filters</article></body></html>"
GPN = "reconstruction-4r40-dashtabad-zaamin-bakhmal-gallaral-road-project-24-182-km-gpn"


def _cfg():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml")))["sources"]
    return [s for s in raw if s["id"] == "isdb"][0]


def _handler(request):
    q = dict(request.url.params)
    if q.get("loc") == "UZ" and q.get("status") == "active":
        return httpx.Response(200, text=UZ_ACTIVE)
    return httpx.Response(200, text=NO_RESULTS)


def _run(cfg=None):
    real = httpx.AsyncClient

    def client(**kw):
        return real(transport=httpx.MockTransport(_handler), **kw)

    adapter = HtmlAdapter(SourceConfig(**(cfg or _cfg())))
    adapter.last_error = None

    async def no_sleep(*_a, **_k):
        return None

    adapter.rate_limit = no_sleep
    html_mod.httpx.AsyncClient = client
    try:
        items = asyncio.run(adapter._fetch_items())
    finally:
        html_mod.httpx.AsyncClient = real
    return items, adapter


def test_list_comes_only_with_the_form_filter():
    items, adapter = _run()
    assert adapter.last_error is None
    assert len(items) == 6
    cfg = _cfg()
    cfg.pop("params")
    assert _run(cfg)[0] == []


def test_every_tender_has_its_own_id_not_the_year():
    items, _ = _run()
    ids = [t.external_id for t in items]
    assert len(set(ids)) == len(ids) == 6
    assert not any(i.isdigit() for i in ids), ids
    assert GPN in ids


def test_link_and_deadline_of_the_road_gpn():
    items, _ = _run()
    t = [x for x in items if x.external_id == GPN][0]
    assert t.source_url == "https://www.isdb.org/project-procurement/tenders/2025/gpn/" + GPN
    assert t.deadline == "2026-10-20"


def test_closed_lots_are_cut_and_live_ones_pass():
    from crawler.core.notifier import _is_deadline_expired
    items, _ = _run()
    now = datetime(2026, 10, 10, 10, 0)
    live = [t for t in items if not _is_deadline_expired(t, now=now)]
    assert sorted(t.deadline for t in live) == ["2026-10-12", "2026-10-20"]


def test_full_english_month_names_are_converted():
    f = html_mod._english_deadline_to_iso
    assert f("20 October 2026") == "2026-10-20"
    assert f("5 September 2025") == "2025-09-05"
    assert f("1 Sept 2026") == "2026-09-01"
    assert f("14-Oct-2026 18:00 (GMT 2.00)") == "2026-10-14T18:00+02:00"
    assert f("12 Foo 2026") == "12 Foo 2026"
    assert f("October 2026") == "October 2026"
