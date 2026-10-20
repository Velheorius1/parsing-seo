"""Старые аукционы UZEX: перебор лотов по номеру (20.10.2026).

ЧТО БЫЛО. У xarid.uzex.uz/auction список закрыт RSA-токеном, и эту систему мы
не читали вовсе: «UZEX Обратные аукционы» — другая система (auctionX, свои
номера). Независимая сверка 20.10 нашла там 629 открытых лотов, среди них печать
книг КГУ 283 млн и НУУз 127,6 млн, плакаты 132 и 75 млн — ни одного в базе.

Здесь держится: опубликованный лот становится тендером со сроком, заказчиком и
позициями; лот до торгов ждёт в очереди и уходит, когда его опубликуют;
завершённый пропускается; перебор останавливается после max_misses «нет лота»
и продолжает с последнего найденного; один сбойный номер не держит перебор.

Run: .venv/bin/python3 -m pytest crawler/tests/test_xarid_probe.py -q
"""
import asyncio
import copy
import json
import os
from unittest.mock import AsyncMock, patch

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters.xarid_probe import XaridProbeAdapter  # noqa: E402
from crawler.auth.session_store import session_store  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
LOTS = json.load(open(os.path.join(_HERE, "fixtures", "xarid_auction_lots_2026-10-20.json"), encoding="utf-8"))
NOT_FOUND = '{"status":500,"message":"База данных : ORA-01403: no data found"}'


def _cfg(**probe):
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml"), encoding="utf-8"))
    cfg = [s for s in raw["sources"] if s["id"] == "xarid-auctions"][0]
    cfg = copy.deepcopy(cfg)
    cfg["probe"].update(probe)
    cfg["rate_limit"] = 1000
    return SourceConfig(**cfg)


def _run(lots, state, errors=(), **probe):
    """lots: {id: dict}. Нет в lots — ORA-01403. Возвращает (тендеры, state, запрошенные id)."""
    asked = []

    class _Resp(object):
        def __init__(self, code, body):
            self.status_code, self.text = code, body

        def json(self):
            return json.loads(self.text)

    class _Client(object):
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url):
            lot_id = int(url.rsplit("/", 1)[1])
            asked.append(lot_id)
            if lot_id in errors:
                return _Resp(502, "Bad Gateway")
            if lot_id in lots:
                return _Resp(200, json.dumps(lots[lot_id], ensure_ascii=False))
            return _Resp(500, NOT_FOUND)

    adapter = XaridProbeAdapter(_cfg(**probe))
    adapter.rate_limit = AsyncMock()
    with patch.object(session_store, "get_setting", return_value=state), \
            patch.object(session_store, "set_setting") as put, \
            patch("crawler.adapters.xarid_probe.httpx.AsyncClient", _Client):
        out = asyncio.run(adapter._fetch_items())
    return out, put.call_args[0][1], asked


def _lot(key, **over):
    lot = copy.deepcopy(LOTS[str(key)])
    lot.update(over)
    return lot


def test_published_lot_becomes_a_tender():
    out, state, _ = _run({419080: _lot(419080)}, {"next_id": 419080})
    assert len(out) == 1, out
    t = out[0]
    assert t.external_id == "419080" and t.source == "Xarid Аукционы", t
    assert t.title.startswith("Услуги издательские: Книги печатные"), t.title
    assert t.organization == "КГУ им Бердаха" and t.price == 283334000.0, t
    assert t.deadline == "2026-10-26T10:13:30", t.deadline
    assert t.source_url == "https://xarid.uzex.uz/auction/detail/419080", t.source_url
    assert "korektor" in t.extra_info["Описание"] and t.extra_info["Номер"] == "26111007419080", t.extra_info
    assert "korektor" not in t.search_text, t.search_text  # описание — в карточку, не в поиск
    assert state["next_id"] == 419081, state


def test_lot_before_auction_waits_in_queue_then_goes_out():
    out, state, _ = _run({418779: _lot(418779)}, {"next_id": 418779})
    assert out == [] and "418779" in state["pending"] and state["next_id"] == 418780, state
    published = _lot(418779, status_name="Опубликован", start_date="2026-10-21T09:00:00",
                     end_date="2026-10-28T10:00:00")
    out, state, asked = _run({418779: published}, state)
    assert asked[0] == 418779 and [t.external_id for t in out] == ["418779"], (asked, out)
    assert state["pending"] == {}, state


def test_finished_lot_is_skipped():
    out, state, _ = _run({417701: _lot(417701)}, {"next_id": 417701})
    assert out == [] and state["pending"] == {} and state["next_id"] == 417702, state


def test_probe_stops_after_misses_and_resumes_from_last_found():
    lots = {100: _lot(419080, id=100), 103: _lot(419080, id=103)}
    out, state, asked = _run(lots, {"next_id": 100}, max_misses=5)
    assert [t.external_id for t in out] == ["100", "103"], out
    assert asked == [100, 101, 102, 103, 104, 105, 106, 107, 108], asked
    assert state["next_id"] == 104, state


def test_single_failing_id_does_not_block_the_probe():
    lots = {100: _lot(419080, id=100), 102: _lot(419080, id=102)}
    out, state, _ = _run(lots, {"next_id": 100}, errors={101}, max_misses=3)
    assert [t.external_id for t in out] == ["100", "102"], out
    assert "101" in state["pending"] and state["next_id"] == 103, state


def test_network_trouble_stops_and_keeps_position():
    out, state, asked = _run({}, {"next_id": 100}, errors={100, 101, 102, 103})
    assert asked == [100, 101, 102] and state["next_id"] == 100, (asked, state)


def test_stale_queue_entries_are_dropped():
    out, state, asked = _run({}, {"next_id": 500, "pending": {"400": "2026-01-01T00:00:00+00:00"}}, max_misses=1)
    assert 400 not in asked and state["pending"] == {}, (asked, state)


def test_book_printing_lot_passes_the_dictionary():
    from crawler.core.notifier import _find_matching_keyword
    out, _, _ = _run({419080: _lot(419080)}, {"next_id": 419080})
    assert _find_matching_keyword(out[0], ["книг", "печат"]) is not None
