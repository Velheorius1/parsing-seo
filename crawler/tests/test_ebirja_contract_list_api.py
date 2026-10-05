"""Список договоров ebirja — из публичного API, а не со страницы сайта.

ИЗ ЧЕГО ВЫРОСЛО. До 05.10.2026 fetch_ebirja_contracts листал страницу в
Playwright, а кнопку «дальше» не находил: 20 договоров каждого типа за прогон,
магазин собирался на ~19% (209 из 1080 за 14–20.09). Строка из API должна быть
той же, что давал разбор карточки, — иначе upsert по (external_id, source)
заведёт второй экземпляр договора вместо обновления.
"""
import asyncio
import sys
from datetime import date
from types import SimpleNamespace

from crawler.scripts import fetch_ebirja_contracts as F
from crawler.scripts.collect_ebirja_contract_api import normalize

CARD = """№ 26521006031792
18.09.2026
Договор № XT26030386
Заказчик:
Mikrokreditbank Qashqadaryo BXO
Цена победителя:
14 545 353 748.8 UZS
Исполнитель:
PRINTUZ MCHJ
Статус:
Принял"""

API = {"id": 32763, "number": "XT26030386", "tender": {"lot": "26521006031792"},
       "customer": {"title": "Mikrokreditbank  Qashqadaryo BXO "},
       "producer": {"title": "PRINTUZ MCHJ"}, "price": 14545353748.8,
       "created_at": "2026-09-18 10:11:12", "currency": "000"}


def _api(n, day="2026-10-04", currency="000", number=None):
    return {"id": n, "number": number or "XD%d" % n, "order": {"lot_number": "L%d" % n},
            "customer": {"title": "Buyer"}, "producer": {"title": "Winner"}, "price": 1000 + n,
            "created_at": "%s 09:00:00" % day, "currency": currency}


def test_api_row_is_the_card_row_of_the_same_contract():
    card = F._parse_contract_text(CARD, "https://ebirja.uz/ru/contracts/tender/32763", "tender")
    api = F.api_row("tender", normalize("tender", API))
    card.pop("collected_at"), api.pop("collected_at")
    assert api == card, {k: (api.get(k), card.get(k)) for k in set(api) | set(card) if api.get(k) != card.get(k)}


def test_currency_code_is_mapped_and_unknown_code_is_not_guessed():
    usd = F.api_row("selection", normalize("selection", _api(1, currency="840")))
    assert usd["currency"] == "USD", "браузер писал сюда UZS при сумме в долларах"
    assert F.api_row("selection", normalize("selection", _api(2, currency="999"))) is None


def _fake_collect(window, page0):
    def collect(source_key, date_from, page_size, page_cap):
        return {"rows": [normalize(source_key, r) for r in window],
                "raw_pages": [{"rows": page0}] if page0 else [],
                "complete": True, "completion": "date_boundary", "pages_collected": 1}
    return collect


def test_empty_window_still_rewrites_the_freshest_contracts():
    """У тендера 3 договора за два месяца: без пульса collected_at стареет за 36 ч."""
    old = [_api(n, day="2026-08-01") for n in range(1, 4)]
    rows, run = F.fetch_contracts_api("tender", date(2026, 10, 2), collect=_fake_collect([], old))
    assert [r["external_id"] for r in rows] == ["ebirja-ctr-XD1", "ebirja-ctr-XD2", "ebirja-ctr-XD3"]
    assert run["complete"]


def test_heartbeat_is_capped_and_overlap_is_not_written_twice():
    page0 = [_api(n) for n in range(1, 101)]
    rows, _ = F.fetch_contracts_api("auction", date(2026, 10, 2),
                                    collect=_fake_collect(page0[:5], page0))
    assert len(rows) == F.HEARTBEAT_ROWS, len(rows)
    assert len({r["external_id"] for r in rows}) == len(rows)


def test_duplicate_contract_number_never_reaches_one_upsert_batch():
    """Один external_id дважды в пачке — Postgres роняет всю пачку."""
    window = [_api(1, number="XD7"), _api(2, number="XD7"), _api(3, currency="999")]
    rows, run = F.fetch_contracts_api("shop", date(2026, 10, 2), collect=_fake_collect(window, []))
    assert [r["external_id"] for r in rows] == ["ebirja-ctr-XD7"]
    assert run["skipped"] == 1


class _Table:
    def __init__(self, log, fail):
        self.log, self.fail, self.ids = log, fail, None

    def select(self, _cols):
        return self

    def in_(self, _col, ids):
        self.ids = ids
        return self

    def execute(self):
        self.log.append(len(self.ids))
        if self.fail:
            raise RuntimeError("502 Bad Gateway")
        return SimpleNamespace(data=[{"external_id": i, "search_text": "x | winner:W"} for i in self.ids[:1]])


class _Client:
    def __init__(self, fail=False):
        self.log, self.fail = [], fail

    def table(self, _name):
        return _Table(self.log, self.fail)


def test_enriched_lookup_goes_in_batches_of_50():
    client = _Client()
    got = F.enriched_search_text(client, ["id%d" % i for i in range(120)])
    assert client.log == [50, 50, 20], client.log
    assert set(got) == {"id0", "id50", "id100"}


def test_enriched_lookup_failure_raises_instead_of_letting_upsert_overwrite():
    sleep, F.time.sleep = F.time.sleep, lambda _s: None
    try:
        client = _Client(fail=True)
        try:
            F.enriched_search_text(client, ["a", "b"])
        except RuntimeError:
            pass
        else:
            raise AssertionError("пустой словарь = затёртые winner:/discount:")
        assert client.log == [2, 2, 2], client.log
    finally:
        F.time.sleep = sleep


def test_incomplete_list_is_written_but_alerts_and_fails_the_run():
    alerts, saved = [], {}
    patched = {
        "fetch_contracts_api": lambda ctype, d: ([F.api_row(ctype, normalize(ctype, _api(1)))],
                                                 {"complete": ctype != "shop", "completion": "page_cap",
                                                  "pages": 500, "skipped": 0}),
        "upsert_to_supabase": lambda rows, dry_run=False: saved.setdefault("n", len(rows)),
        "_send_telegram_alert": alerts.append,
    }
    orig = {k: getattr(F, k) for k in patched}
    for k, v in patched.items():
        setattr(F, k, v)
    try:
        args = SimpleNamespace(type="all", since=None, days=3, detail=False, detail_limit=20, dry_run=False)
        try:
            asyncio.run(F.main_async(args))
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("неполный список прошёл молча")
    finally:
        for k, v in orig.items():
            setattr(F, k, v)
    assert saved["n"] == 4, "собранное всё равно пишем"
    assert len(alerts) == 1 and "shop (page_cap)" in alerts[0], alerts


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            print("FAIL", test.__name__, "-", repr(exc)[:200])
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
