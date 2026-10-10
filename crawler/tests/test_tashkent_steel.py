"""Tashkent Steel (ТМЗ) снова собирается, а поломка HTML-источника не молчит (10.10.2026).

ЧТО БЫЛО. Портал xarid.tashkentsteel.uz с 05.06.2026 выпустил 62 лота, 2-3 в
неделю, среди них наш профиль: «Сувенирный набор инструментов с логотипом TMZ»
(лот №481-2026, до 14.10), этикетки, баннеры. Источник был включён, но искал
заголовок по `h2.elementor-heading-title`, а сайт уже на своей вёрстке:
карточки (`a[href*='/lot/']`) находились, заголовка не было, и каждая карточка
отбрасывалась. HTML-адаптер возвращал «0 строк, ошибок нет» — то же самое, что
«площадка ничего не публикует». За всё время источник собрал одну строку.
Тем же тихим нулём заканчивался и сбой загрузки (10.10 у портала истёк
сертификат; Ипотека-банк и UNGM падали так каждый прогон).

Здесь держится: конфиг из sources.yaml разбирает настоящий срез страницы
(заголовок, срок с поясом, ссылка, id); старые селекторы на этой же странице
дают ОШИБКУ прогона, а не тихий ноль; сбой загрузки — тоже ошибка; отсев
фильтром страны (UNDP) ошибкой не считается.

Run: python3 -m crawler.tests.test_tashkent_steel   (exit 1 on any failure)
"""
import asyncio
import os
import sys
import types

import yaml

if "pydantic_settings" not in sys.modules:
    _stub = types.ModuleType("pydantic_settings")

    class _BaseSettings(object):
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    _stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = _stub

from crawler.adapters.html import HtmlAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SOURCES = os.path.join(_REPO, "crawler", "config", "sources.yaml")
PAGE = open(os.path.join(_HERE, "fixtures", "tashkentsteel_home_2026-10-10.html")).read()
URL = "https://xarid.tashkentsteel.uz/"


def _live_cfg():
    raw = yaml.safe_load(open(_SOURCES))["sources"]
    return [s for s in raw if s["id"] == "tashkent-steel"][0]


def _parse(cfg_dict):
    adapter = HtmlAdapter(SourceConfig(**cfg_dict))
    adapter.last_error = None
    return adapter, adapter._parse_page(PAGE, URL)


def test_live_config_parses_every_card():
    _, items = _parse(_live_cfg())
    assert len(items) == 2, [t.title for t in items]


def test_profile_lot_is_read_fully():
    _, items = _parse(_live_cfg())
    lot = [t for t in items if t.external_id == "57"]
    assert lot, [t.external_id for t in items]
    t = lot[0]
    assert t.title == "Сувенирный набор инструментов с логотипом TMZ", t.title
    assert t.deadline == "2026-10-14T23:29:00+05:00", t.deadline
    assert t.source_url == "https://xarid.tashkentsteel.uz/lot/57", t.source_url
    assert t.id == "tmz-57"
    assert t.organization == "Tashkent Steel (ТМЗ)"
    assert t.currency == "UZS"


def test_deadline_is_exact_and_not_expired_before_time():
    # Срок с поясом: 14.10 23:29 по Ташкенту = 18:29 UTC. За минуту до — живой,
    # через минуту после — истёк. Разбор времени — PR #83.
    from datetime import datetime
    from crawler.core.notifier import _is_deadline_expired
    _, items = _parse(_live_cfg())
    t = [x for x in items if x.external_id == "57"][0]
    assert _is_deadline_expired(t, now=datetime(2026, 10, 14, 18, 28)) is False
    assert _is_deadline_expired(t, now=datetime(2026, 10, 14, 18, 30)) is True


def test_old_elementor_selectors_are_loud_not_silent():
    cfg = _live_cfg()
    cfg["html_selectors"] = {"container": "a[href*='/lot/']",
                             "title": "h2.elementor-heading-title:nth-match(4)",
                             "deadline": "h2.elementor-heading-title:nth-match(8)",
                             "link": "@href"}
    adapter, items = _parse(cfg)
    assert items == []
    assert adapter.last_error and "селекторы устарели" in adapter.last_error, adapter.last_error


def test_working_selectors_leave_no_error():
    adapter, _ = _parse(_live_cfg())
    assert adapter.last_error is None, adapter.last_error


def test_country_filter_dropping_all_is_not_an_error():
    cfg = _live_cfg()
    cfg["country_filter"] = "UZB-NOWHERE"
    adapter, items = _parse(cfg)
    assert items == []
    assert adapter.last_error is None, adapter.last_error


def test_fetch_failure_is_reported():
    adapter = HtmlAdapter(SourceConfig(**_live_cfg()))
    adapter.last_error = None

    async def failing(client, url):
        adapter._fetch_error = "ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
        return None

    adapter._fetch_page = failing
    items = asyncio.run(adapter._fetch_items())
    assert items == []
    assert adapter.last_error and "CERTIFICATE_VERIFY_FAILED" in adapter.last_error, adapter.last_error


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
