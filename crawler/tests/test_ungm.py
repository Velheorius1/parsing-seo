"""UN Global Marketplace снова собирается (10.10.2026).

ЧТО БЫЛО. За всю историю источник не дал ни одной строки. Поиск UNGM — POST,
который сервер принимает только с антифорджери-токеном ASP.NET (скрытое поле
страницы → заголовок RequestVerificationToken) и с cookie той же сессии; без
них — 400/403 «Bad Request». Бот-UA «TenderMonitor/1.0» режется уже на GET,
а PageSize 50 сервер отвергает (только 15). Ошибка была тихой до PR #86, потом
краснела в каждом полном прогоне.

А профиль там есть: ILO «Request for quotations: printing of Better Work
Uzbekistan Information Flyers…», срок 14.10.2026.

Здесь держится: адаптер берёт токен и cookie со страницы и шлёт поиск с ними;
нет токена на странице — громкая ошибка, а не тихий ноль; срок UNGM
переводится в ISO с поясом, и префильтр отсекает лот по точному времени.

Run: .venv/bin/python3 -m pytest crawler/tests/test_ungm.py -q
"""
import asyncio
import json
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
from crawler.adapters.html import HtmlAdapter, _english_deadline_to_iso  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
SEARCH = open(os.path.join(_HERE, "fixtures", "ungm_search_uz_2026-10-10.html"), encoding="utf-8").read()
PAGE = "https://www.ungm.org/Public/Notice"
TOKEN_PAGE = ('<html><form><input name="__RequestVerificationToken" type="hidden" '
              'value="tok-123" /></form></html>')
PRINTING_ID = "317201"


def _cfg():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml")))["sources"]
    return [s for s in raw if s["id"] == "ungm"][0]


def _server(token_page=TOKEN_PAGE):
    """Контракт UNGM, каким он оказался 10.10.2026."""
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url)))
        if request.method == "GET" and str(request.url) == PAGE:
            if "TenderMonitor" in request.headers.get("user-agent", ""):
                return httpx.Response(403, text="Forbidden" * 20)
            return httpx.Response(200, text=token_page,
                                  headers={"set-cookie": "__RequestVerificationToken=c-1; path=/"})
        if request.method == "POST" and str(request.url) == PAGE + "/Search":
            body = json.loads(request.content.decode("utf-8"))
            ok = (request.headers.get("RequestVerificationToken") == "tok-123"
                  and "c-1" in request.headers.get("cookie", "")
                  and body.get("PageSize") == 15)
            if not ok:
                return httpx.Response(400, text="Bad Request")
            return httpx.Response(200, text=SEARCH)
        return httpx.Response(404, text="nope")

    return handler, seen


def _run(cfg=None, token_page=TOKEN_PAGE):
    handler, seen = _server(token_page)
    real = httpx.AsyncClient

    def client(**kw):
        return real(transport=httpx.MockTransport(handler), **kw)

    adapter = HtmlAdapter(SourceConfig(**(cfg or _cfg())))
    adapter.last_error = None

    async def no_sleep(*_a, **_k):
        return None

    adapter.rate_limit = no_sleep
    html_mod.httpx.AsyncClient = client
    orig_sleep = html_mod.asyncio.sleep
    html_mod.asyncio.sleep = no_sleep
    try:
        items = asyncio.run(adapter._fetch_items())
    finally:
        html_mod.httpx.AsyncClient = real
        html_mod.asyncio.sleep = orig_sleep
    return items, adapter, seen


def test_search_goes_with_token_and_cookie():
    items, adapter, seen = _run()
    assert adapter.last_error is None, adapter.last_error
    assert seen[0] == ("GET", PAGE)
    assert len(items) == 15


def test_printing_rfq_is_in_the_feed_with_link_agency_and_deadline():
    items, _, _ = _run()
    t = [x for x in items if x.external_id == PRINTING_ID][0]
    assert t.title.startswith("Request for quotations: printing of Better Work Uzbekistan")
    assert t.organization == "ILO"
    assert t.source_url == "https://www.ungm.org/Public/Notice/317201"
    assert t.deadline == "2026-10-14T18:00+02:00"


def test_closed_by_exact_deadline_time():
    from crawler.core.notifier import _is_deadline_expired
    items, _, _ = _run()
    t = [x for x in items if x.external_id == PRINTING_ID][0]
    assert _is_deadline_expired(t, now=datetime(2026, 10, 14, 15, 59)) is False
    assert _is_deadline_expired(t, now=datetime(2026, 10, 14, 16, 1)) is True


def test_without_antiforgery_step_server_refuses_loudly():
    cfg = _cfg()
    cfg.pop("antiforgery_page")
    items, adapter, _ = _run(cfg)
    assert items == []
    assert "400" in (adapter.last_error or "")


def test_page_without_token_is_a_loud_error_not_a_silent_zero():
    items, adapter, seen = _run(token_page="<html>maintenance</html>")
    assert items == []
    assert "__RequestVerificationToken" in (adapter.last_error or "")
    assert all(method == "GET" for method, _ in seen)


def test_config_matches_what_the_server_accepts():
    cfg = _cfg()
    assert cfg["antiforgery_page"] == PAGE
    assert cfg["body"]["PageSize"] == 15
    assert cfg["body"]["Countries"] == ["2510"]
    assert cfg["body"]["IsActive"] is True
    assert "TenderMonitor" not in cfg["headers"]["User-Agent"]


def test_english_deadline_conversion():
    assert _english_deadline_to_iso("14-Oct-2026 18:00\n   (GMT 2.00)") == "2026-10-14T18:00+02:00"
    assert _english_deadline_to_iso("23-Oct-2026 09:00 (GMT -4.00)") == "2026-10-23T09:00-04:00"
    assert _english_deadline_to_iso("21-Oct-2026 16:30 (GMT 00.00)") == "2026-10-21T16:30+00:00"
    assert _english_deadline_to_iso("5-Nov-2026 10:00") == "2026-11-05T10:00"
    assert _english_deadline_to_iso("14-Oct-2026") == "2026-10-14"
    assert _english_deadline_to_iso("15.05.2026") == "15.05.2026"
    assert _english_deadline_to_iso("12-Xyz-2026") == "12-Xyz-2026"
