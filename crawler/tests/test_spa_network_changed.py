"""SPA-страница переживает смену сети на хосте (10.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. ebirja-announcements падал с «Page.goto: net::ERR_NETWORK_CHANGED»
в 06:00 и 08:00 подряд (8 раз с 25.09). По journalctl совпало до секунды: в
:00:01 cron `run_erp_telegram.sh --process-sources` поднимает контейнер
winch-bot (`docker compose run --rm`), в :00:05 он гаснет, и Chromium рвёт то,
что шло в этот момент. Площадка тут ни при чём.

A/B на проде 10.10 (старый и новый адаптер одновременно у границы 5 минут)
показал два лица одного сбоя: старт на :03 — падает goto; старт на :02 — goto
успел, но рвётся XHR с данными, таблица не рисуется, и оба ждали рендер 45 с.

Здесь закреплено: смена сети в goto или в запросе страницы (requestfailed)
повторяет загрузку целиком, до 3 попыток; таймаут без такой улики уходит
наверх сразу — проблема площадки повторами не маскируется; три смены сети
подряд — тоже громкая ошибка.

Run: .venv/bin/python3 -m pytest crawler/tests/test_spa_network_changed.py -q
"""
import asyncio
import sys
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.adapters.spa as spa_mod  # noqa: E402
from crawler.adapters.spa import SpaAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

URL = "https://ebirja.uz/ru/trade/announcements"
GOTO_NET = "Page.goto: net::ERR_NETWORK_CHANGED at %s" % URL
WAIT_TIMEOUT = "Page.wait_for_selector: Timeout 45000ms exceeded."


def _cfg():
    return SourceConfig(
        id="ebirja-announcements", name="E-Birja объявления торгов", adapter="spa",
        url=URL, timeout=45, id_prefix="ebirja-ann", wait_selector="table",
        html_selectors={"container": "table tbody tr", "title": "td:nth-child(4)"},
    )


class _Request(object):
    def __init__(self, url, failure):
        self.url = url
        self.failure = failure


class _Page(object):
    """Попытки по сценарию: ok | goto_net | xhr_net | wait_timeout."""

    def __init__(self, script):
        self.script = list(script)
        self.current = None
        self.gotos = 0
        self.handlers = []

    def on(self, event, handler):
        assert event == "requestfailed"
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.handlers.remove(handler)

    async def goto(self, url, **kw):
        self.gotos += 1
        self.current = self.script.pop(0) if self.script else "ok"
        if self.current == "goto_net":
            raise Exception(GOTO_NET)

    async def wait_for_selector(self, *a, **kw):
        if self.current == "xhr_net":
            for h in list(self.handlers):
                h(_Request("https://ebirja.uz/api/trade/announcements", "net::ERR_NETWORK_CHANGED"))
            raise Exception(WAIT_TIMEOUT)
        if self.current == "wait_timeout":
            for h in list(self.handlers):
                h(_Request("https://ebirja.uz/favicon.ico", "net::ERR_ABORTED"))
            raise Exception(WAIT_TIMEOUT)

    async def wait_for_timeout(self, *a, **kw):
        return None

    async def query_selector_all(self, *a, **kw):
        return []

    async def close(self):
        return None


def _fake_playwright(page):
    class _Browser(object):
        async def new_page(self):
            return page

        async def close(self):
            return None

    class _Chromium(object):
        async def launch(self, **kw):
            return _Browser()

    class _PW(object):
        chromium = _Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    mod = types.ModuleType("playwright.async_api")
    mod.async_playwright = lambda: _PW()
    return mod


def _run(script):
    page = _Page(script)
    saved = sys.modules.get("playwright.async_api")
    sys.modules["playwright.async_api"] = _fake_playwright(page)
    orig_pause = getattr(spa_mod, "_GOTO_RETRY_PAUSE_S", None)
    spa_mod._GOTO_RETRY_PAUSE_S = 0.0
    try:
        try:
            result = asyncio.run(SpaAdapter(_cfg())._fetch_items())
            error = None
        except Exception as exc:  # noqa: BLE001
            result, error = None, exc
    finally:
        spa_mod._GOTO_RETRY_PAUSE_S = orig_pause
        if saved is None:
            sys.modules.pop("playwright.async_api", None)
        else:
            sys.modules["playwright.async_api"] = saved
    return result, error, page


def test_network_change_in_goto_is_retried_and_the_page_is_read():
    result, error, page = _run(["goto_net"])
    assert error is None, error
    assert result == []
    assert page.gotos == 2


def test_network_change_in_page_request_is_retried():
    result, error, page = _run(["xhr_net"])
    assert error is None, error
    assert page.gotos == 2


def test_two_network_changes_still_recover():
    result, error, page = _run(["goto_net", "xhr_net"])
    assert error is None, error
    assert page.gotos == 3


def test_persistent_network_change_is_a_loud_error():
    _, error, page = _run(["goto_net", "xhr_net", "goto_net"])
    assert error is not None
    assert page.gotos == 3


def test_site_timeout_without_network_change_is_not_retried():
    _, error, page = _run(["wait_timeout"])
    assert error is not None and "Timeout" in str(error)
    assert page.gotos == 1


def test_clean_load_goes_once_and_listener_is_removed():
    result, error, page = _run([])
    assert error is None and result == []
    assert page.gotos == 1
    assert page.handlers == []
