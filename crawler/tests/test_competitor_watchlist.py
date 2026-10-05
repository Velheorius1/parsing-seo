"""Список ~30 конкурентов: ранжирование побед и реестр (02.10.2026).

Свойства:
  • окна ранжирования вплотную и ровно на `days` — без дыр и без двойного счёта;
  • фирма без 9-значного ИНН (ПИНФЛ, мусор) в список не попадает, но и не теряется
    молча — счётчик пропусков;
  • порядок — по числу побед, при равенстве — по сумме;
  • реестр валиден, в нём 25–32 фирмы из рейтинга с ИНН плюс закреплённые Данияром
    (`pinned`, список 13.09 — с 05.10), а выбывшие (`retired`) не попадают ни в ★,
    ни в блок «Список», ни в монитор.

Run: python3 -m crawler.tests.test_competitor_watchlist   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta, timezone

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core import competitor_wins as CW  # noqa: E402
from crawler.core.competitor_audit import load_registry, registry_inns  # noqa: E402
from crawler.scripts import competitor_watchlist as L  # noqa: E402

NOW = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)


def _win(inn, price=10e6, name="FIRM", feed="deals"):
    winner = "%s (ИНН %s)" % (name, inn) if inn else name
    return {"win": {"winner": winner, "winner_inn": CW.winner_inn(winner), "won_price": price,
                    "start_price": price * 1.25, "currency": "UZS", "customer": "MAKTAB", "feed": feed,
                    "source_url": "https://etender.uzex.uz/lot/1", "title": "Bosma mahsulotlar",
                    "deal_date": "2026-09-30"}}


def test_windows_tile_the_period_exactly():
    ws = L.windows(NOW, 120)
    assert len(ws) == 4
    assert ws[0][1] == NOW - CW.SAFETY_LAG
    assert all(ws[i][0] == ws[i + 1][1] for i in range(len(ws) - 1)), "дыра или нахлёст между окнами"
    assert ws[0][1] - ws[-1][0] == timedelta(days=120)
    odd = L.windows(NOW, 45)
    assert [(e - s).days for s, e in odd] == [30, 15]


def test_aggregate_counts_profile_and_hidden_and_skips_non_inn():
    reports = [{"items": [_win("123456789"), _win("123456789", feed="civil"), _win("41302751590065")],
                "printers": [_win("123456789", price=5e6), _win(None)]},
               {"items": [_win("987654321", price=99e6)], "printers": []}]
    firms, skipped = L.aggregate(reports)
    assert set(firms) == {"123456789", "987654321"}, firms.keys()
    a = firms["123456789"]
    assert (a["n"], a["profile"], a["hidden"]) == (3, 2, 1), a
    assert a["sum_uzs"] == 25e6 and a["feeds"] == {"deals": 2, "civil": 1}
    assert skipped == {"no_inn": 1, "pinfl": 1}, skipped


def test_rank_by_wins_then_money():
    reports = [{"items": [_win("111111111", 1e6), _win("111111111", 1e6),
                          _win("222222222", 50e6), _win("333333333", 70e6)], "printers": []}]
    firms, _ = L.aggregate(reports)
    order = [r["inn"] for r in L.rank(firms, top=0)]
    assert order == ["111111111", "333333333", "222222222"], order
    assert len(L.rank(firms, top=2)) == 2


def test_registry_holds_the_watchlist_and_retired_stay_out():
    import json
    from crawler.core.competitor_audit import registry_path
    reg = load_registry()
    active = [e for e in reg["entities"] if e.get("inn")]
    ranked = [e for e in active if not e.get("pinned")]
    assert 25 <= len(ranked) <= 32, len(ranked)
    with open(registry_path(), encoding="utf-8") as fh:
        raw = json.load(fh)
    retired = {str(e.get("inn")) for e in raw.get("retired") or [] if e.get("inn")}
    watched = set(CW.watch_map(reg))
    starred = set(registry_inns(reg))
    assert not (retired & watched) and not (retired & starred), retired & (watched | starred)
    for e in reg["entities"]:
        assert e.get("basis") and e.get("sources"), "у фирмы списка должно быть основание: %s" % e["name"]


def test_owner_pinned_firms_stay_watched():
    """Список Данияра 13.09 закреплён 05.10: нет побед на etender — не повод убирать,
    фирма может выигрывать на Cooperation/Hayotbirja, где победителя видно плохо."""
    reg = load_registry()
    pinned = {e["inn"] for e in reg["entities"] if e.get("pinned")}
    for inn in ("303743362", "204695568", "205353003", "305970088"):   # Micros Pak, Credo, Kolorpak, SP Books
        assert inn in pinned, inn
    assert pinned <= set(CW.watch_map(reg)) and pinned <= set(registry_inns(reg))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
