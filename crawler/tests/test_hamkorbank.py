"""Хамкорбанк снова собирается, и закрытые конкурсы не идут в алерты (10.10.2026).

ЧТО БЫЛО. Источник молчал с мая. Антибот-защиту, которую подозревали в июне,
банк снял: страница отдаётся целиком и с Мака, и с VPS. Сменилась вёрстка:
карточки стали прямыми ссылками в div.grid с относительным href от <base>,
класса div.wraps больше нет — старые селекторы не находили ни одной карточки.
Молчание при этом числилось «объяснённым»: якобы покрытие даёт TG-канал банка,
у которого алерты выключены. А профиль у банка бывает: «Брендированные
корпоративные подарочные наборы» 13-19.11.2025.

На карточке только дата публикации, срок подачи — на странице лота. Окно у
банка короткое (6-11 дней), и без срока закрытые конкурсы считались бы живыми.
Поэтому срок дотягивается со страницы лота (`detail_deadline_regex`).

Здесь держится: конфиг из sources.yaml берёт только карточки тендеров (не меню
и не соседнюю сетку банковских карт), ссылка и id верные, дата публикации не
становится сроком; срок со страницы лота разбирается и закрытый лот отсекается
префильтром; страницы лотов дотягиваются только без срока и в пределах бюджета.

Run: python3 -m crawler.tests.test_hamkorbank   (exit 1 on any failure)
"""
import asyncio
import os
import sys
import types
from datetime import datetime

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
LIST = open(os.path.join(_HERE, "fixtures", "hamkorbank_list_2026-10-10.html")).read()
LOT = open(os.path.join(_HERE, "fixtures", "hamkorbank_lot_2026-10-10.html")).read()
URL = "https://hamkorbank.uz/press-center/tenders/"


def _cfg():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml")))["sources"]
    return [s for s in raw if s["id"] == "hamkorbank"][0]


def _adapter(cfg=None):
    a = HtmlAdapter(SourceConfig(**(cfg or _cfg())))
    a.last_error = None
    return a


def _run(pages, cfg=None):
    """pages: url -> html. Возвращает (items, запрошенные url, adapter)."""
    a = _adapter(cfg)
    asked = []

    async def fake_fetch(client, url):
        asked.append(url)
        return pages.get(url)

    a._fetch_page = fake_fetch
    items = asyncio.run(a._fetch_items())
    return items, asked, a


LOT_URL = "https://hamkorbank.uz/press-center/tenders/bank-systems-load-testing-services/"


def test_only_tender_cards_are_taken():
    items = _adapter()._parse_page(LIST, URL)
    assert [t.external_id for t in items] == [
        "bank-systems-load-testing-services",
        "tenable-vulnerability-management-system-license",
        "kaspersky-embedded-system-security",
    ], [t.external_id for t in items]


def test_card_fields():
    t = _adapter()._parse_page(LIST, URL)[0]
    assert "\xa0" not in t.title, repr(t.title)
    assert t.title.startswith("Открытый конкурс на услуги по проведению нагрузочного"), t.title
    assert t.source_url == LOT_URL, t.source_url
    assert t.id == "hmkbnk-bank-systems-load-testing-services"
    assert t.organization == "Хамкорбанк"
    # дата карточки — публикация, а не срок подачи
    assert t.deadline is None and t.date_start == "18 сентября 2026", (t.deadline, t.date_start)


def test_deadline_comes_from_the_lot_page():
    items, asked, _ = _run({URL: LIST, LOT_URL: LOT})
    t = [x for x in items if x.source_url == LOT_URL][0]
    assert t.deadline == "29.09.2026", t.deadline
    assert LOT_URL in asked


def test_closed_lot_is_cut_by_prefilter():
    from crawler.core.notifier import _is_deadline_expired
    items, _, _ = _run({URL: LIST, LOT_URL: LOT})
    t = [x for x in items if x.source_url == LOT_URL][0]
    assert _is_deadline_expired(t, now=datetime(2026, 10, 10, 6, 0)) is True
    assert _is_deadline_expired(t, now=datetime(2026, 9, 25, 6, 0)) is False


def test_lot_without_deadline_text_stays_unknown():
    items, _, _ = _run({URL: LIST})  # страницы лотов не отдались
    assert all(t.deadline is None for t in items)


def test_detail_budget_is_respected():
    cfg = _cfg()
    cfg["html_selectors"] = dict(cfg["html_selectors"], detail_max=1)
    _, asked, _ = _run({URL: LIST, LOT_URL: LOT}, cfg)
    assert len([u for u in asked if u != URL]) == 1, asked


def test_single_digit_day_is_padded():
    lot = LOT.replace("29.09.2026", "5.10.2026")
    items, _, _ = _run({URL: LIST, LOT_URL: lot})
    t = [x for x in items if x.source_url == LOT_URL][0]
    assert t.deadline == "05.10.2026", t.deadline


def test_sources_without_detail_regex_fetch_nothing_extra():
    cfg = _cfg()
    cfg["html_selectors"] = {k: v for k, v in cfg["html_selectors"].items()
                             if k not in ("detail_deadline_regex", "detail_max")}
    _, asked, _ = _run({URL: LIST, LOT_URL: LOT}, cfg)
    assert asked == [URL], asked


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
