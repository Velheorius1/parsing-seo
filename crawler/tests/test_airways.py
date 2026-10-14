"""Uzbekistan Airways: срок подачи — со страницы лота, с часом (14.10.2026).

ЧТО БЫЛО. На карточке ленты одна дата — `time@datetime`, и это момент
ПУБЛИКАЦИИ («2026-10-05T17:42:06+05:00»), а не срок. Пока срок жил до конца
дня, лот успевал уйти в алерт в день публикации. С #83 срок со временем
истекает в этот час — и каждый лот Airways умирал на первом же прогоне: у всех
11 строк за месяц «срок» раньше момента, когда мы лот впервые увидели.
Регресс-прогон 14.10: «Изготовление и поставка рекламно-сувенирной продукции»
на 2027 год (алерт 05.10, срок 09.10 16:00) сегодня бы потерялся.

Настоящий срок — в тексте лота, с часом по Ташкенту, в трёх формулировках
(все три — живые страницы 14.10, фикстуры урезаны до тела объявления).

Здесь держится: дата карточки уходит в date_start; срок со страницы лота
пишется с часом и поясом; лот живёт до этого часа и не дольше; страница без
фразы о приёме (и страховка со «сроком действия полиса») срока не получает.

Run: python3 -m crawler.tests.test_airways   (exit 1 on any failure)
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
_FX = os.path.join(_HERE, "fixtures")
URL = "https://corp.uzairways.com/ru/press-center/tenders"
BASE = "https://corp.uzairways.com/index.php/ru/press-center/tenders/"


def _fx(name):
    return open(os.path.join(_FX, "airways_%s_2026-10-14.html" % name), encoding="utf-8").read()


LIST = _fx("list")
LOTS = {
    BASE + "izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii": _fx("lot_souvenir"),
    BASE + "priobretenie-zapasnykh-chastey-dlya-regulyarnykh-i-tyazhelykh-form-68": _fx("lot_spare_parts"),
    BASE + "okazanie-yuridicheskikh-i-konsultacionnykh-uslug-nezavisimoy-mezhdunarodnoy": _fx("lot_legal"),
    BASE + "obyazatelnoe-strakhovanie-grazhdanskoy-otvetstvennosti-rabotodatelya-0": _fx("lot_insurance"),
    BASE + "uslugi-po-transportirovke-aviacionnogo-dvigatelya-genx-lb-esn-956160": _fx("lot_engine"),
}


def _cfg():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml")))["sources"]
    return [s for s in raw if s["id"] == "uz-airways"][0]


def _run(pages):
    a = HtmlAdapter(SourceConfig(**_cfg()))
    a.last_error = None
    asked = []

    async def fake_fetch(client, url):
        asked.append(url)
        return pages.get(url)

    a._fetch_page = fake_fetch
    return asyncio.run(a._fetch_items()), asked


def _by_slug(items, slug):
    return [t for t in items if t.source_url == BASE + slug][0]


def test_card_date_is_publication_not_deadline():
    items = HtmlAdapter(SourceConfig(**_cfg()))._parse_page(LIST, URL)
    assert len(items) == 9, len(items)
    t = _by_slug(items, "izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii")
    assert t.deadline is None, t.deadline
    assert t.date_start and t.date_start.startswith("2026-10-05T17:42"), t.date_start


def test_external_id_is_the_whole_slug():
    """«-0» в конце slug — не id: два разных лота с «-0» сливались в одну строку."""
    items = HtmlAdapter(SourceConfig(**_cfg()))._parse_page(LIST, URL)
    ids = [t.external_id for t in items]
    assert len(set(ids)) == len(ids) == 9, ids
    assert "ao-uzbekistan-airways-provodit-sbor-kommercheskikh-predlozheniy-na-zakupku-0" in ids, ids
    assert "obyazatelnoe-strakhovanie-grazhdanskoy-otvetstvennosti-rabotodatelya-0" in ids, ids
    # лоты без цифр в slug сохраняют прежний id — без дублей в базе
    assert "izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii" in ids, ids
    assert not [i for i in ids if i.isdigit()], ids


def test_deadline_with_hour_from_the_lot_page():
    items, _ = _run(dict(LOTS, **{URL: LIST}))
    got = dict((t.source_url[len(BASE):], t.deadline) for t in items if t.source_url.startswith(BASE))
    # «до 09.10.2026 года время 16:00»
    assert got["izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii"] == "2026-10-09T16:00:00+05:00", got
    # «Срок окончания приема …: до 14.10.2026 г. 17-00 часов»
    assert got["priobretenie-zapasnykh-chastey-dlya-regulyarnykh-i-tyazhelykh-form-68"] == \
        "2026-10-14T17:00:00+05:00", got
    # «до 13.10.2026 года, время 16:00»
    assert got["okazanie-yuridicheskikh-i-konsultacionnykh-uslug-nezavisimoy-mezhdunarodnoy"] == \
        "2026-10-13T16:00:00+05:00", got


def test_pages_without_acceptance_phrase_stay_unknown():
    items, _ = _run(dict(LOTS, **{URL: LIST}))
    # страховка: «Срок окончания действия полиса … 5 октября 2026» — не срок подачи
    assert _by_slug(items, "obyazatelnoe-strakhovanie-grazhdanskoy-otvetstvennosti-rabotodatelya-0").deadline is None
    assert _by_slug(items, "uslugi-po-transportirovke-aviacionnogo-dvigatelya-genx-lb-esn-956160").deadline is None


def test_lot_lives_until_the_hour_not_the_day():
    from crawler.core.notifier import _is_deadline_expired
    items, _ = _run(dict(LOTS, **{URL: LIST}))
    t = _by_slug(items, "izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii")
    # первое появление 05.10 14:03 UTC — раньше лот умирал именно здесь
    assert _is_deadline_expired(t, now=datetime(2026, 10, 5, 14, 3)) is False
    # 09.10 15:00 по Ташкенту — ещё принимают
    assert _is_deadline_expired(t, now=datetime(2026, 10, 9, 10, 0)) is False
    # 09.10 16:30 по Ташкенту — приём закрыт
    assert _is_deadline_expired(t, now=datetime(2026, 10, 9, 11, 30)) is True


def test_impossible_hour_keeps_the_date_only():
    lot = LOTS[BASE + "izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii"].replace("16:00", "26:00")
    pages = dict(LOTS, **{URL: LIST})
    pages[BASE + "izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii"] = lot
    items, _ = _run(pages)
    t = _by_slug(items, "izgotovlenie-i-postavka-reklamno-suvenirnoy-produkcii")
    assert t.deadline == "09.10.2026", t.deadline


def test_all_cards_fit_the_detail_budget():
    _, asked = _run(dict(LOTS, **{URL: LIST}))
    assert len([u for u in asked if u != URL]) == 9, asked


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
