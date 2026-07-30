"""Пины «второго шанса» (30.07).

Механизм досылает лоты, которых конвейер не увидел с первого раза. Все три
свойства ниже уже ломались в ходе замеров, поэтому и закреплены:

1. Выборка обязана требовать И `alert_seq IS NULL`, И `relevance_score IS NULL`.
   Без второго условия крон каждый день переспрашивал бы AI об одних и тех же
   отвергнутых лотах и жёг деньги.
2. Судить надо СЕГОДНЯШНИМ днём. Первый замер шёл as_of=дата сбора и обещал
   18 находок из 60; под сегодняшним днём просроченные отсеиваются, как и должны.
3. Фиды историй (завершённые сделки, предложения продавцов) исключены. Именно
   они дали 1 115 из 2 076 «выживших» в первом замере — цемент и асфальт,
   прошедшие по слову «упаковка» из фразы «без упаковки».

Run: python3 -m crawler.tests.test_recheck   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta, timezone


def _load():
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:      # pydantic_settings — прод-зависимость
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(
            alert_keywords=["картон", "коробк"], telegram_bot_token="",
            telegram_alert_chat_id="", openrouter_api_key="")
        sys.modules[cfg] = m
    import crawler.scripts.recheck as R
    return R


R = _load()


def _row(**k):
    k.setdefault("external_id", "e1")
    k.setdefault("source", "XT-Xarid тендеры")
    k.setdefault("title", "Коробка картонная")
    k.setdefault("organization", "АО Банк")
    k.setdefault("search_text", k["title"])
    k.setdefault("price", 50_000_000)
    k.setdefault("message_type", "tender")
    k.setdefault("collected_at", "2026-07-20T10:00:00+00:00")
    return k


# ── 1. условия выборки ────────────────────────────────────────────────────────

def test_query_requires_never_alerted_and_never_scored():
    f = R.day_filters("2026-07-20", "2026-07-21", 5_000_000, frozenset())
    assert ("is_", ("alert_seq", "null")) in f, f
    assert ("is_", ("relevance_score", "null")) in f, f


def test_query_carries_price_floor_and_day_window():
    f = R.day_filters("2026-07-20", "2026-07-21", 7_000_000, frozenset())
    assert ("gte", ("price", 7_000_000)) in f
    assert ("gte", ("collected_at", "2026-07-20")) in f
    assert ("lt", ("collected_at", "2026-07-21")) in f


def test_query_excludes_sources_demoted_in_prod():
    f = R.day_filters("2026-07-20", "2026-07-21", 5_000_000,
                      frozenset({"XT-Xarid э-магазин"}))
    assert ("neq", ("source", "XT-Xarid э-магазин")) in f, f


# ── 2. судим сегодняшним днём ─────────────────────────────────────────────────

def test_lot_open_at_collection_but_expired_today_does_not_survive():
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat()
    keep, counters = R.survivors(
        [_row(deadline=long_ago, collected_at=long_ago + "T10:00:00+00:00")],
        keywords=["картон"], tnved_scope=[])
    assert keep == [], keep
    assert counters.get("deadline_expired") == 1, counters


def test_lot_with_future_deadline_survives():
    soon = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
    keep, _ = R.survivors([_row(deadline=soon)], keywords=["картон"], tnved_scope=[])
    assert len(keep) == 1, keep


def test_lot_without_deadline_survives_and_is_left_to_the_live_check():
    # У 64% отправленных алертов дедлайн пуст (замер 30.07) — отсекать их значит
    # выкинуть основные источники. Живой лот тут подтверждает verifier при отправке.
    keep, _ = R.survivors([_row(deadline=None)], keywords=["картон"], tnved_scope=[])
    assert len(keep) == 1, keep


def test_non_matching_lot_is_dropped():
    keep, counters = R.survivors([_row(title="Цемент навалом", search_text="Цемент")],
                                 keywords=["картон"], tnved_scope=[])
    assert keep == []
    assert counters.get("no_keyword") == 1, counters


# ── 3. фиды историй не участвуют ──────────────────────────────────────────────

def test_history_feeds_are_skipped():
    assert "E-Birja завершённые сделки" in R._SKIP_SOURCES
    assert "E-Birja товары на продажу" in R._SKIP_SOURCES
    assert "UZEX Результаты" in R._SKIP_SOURCES


def test_biddable_source_is_not_skipped():
    for s in ("XT-Xarid тендеры", "UZEX Предквалификации", "ETender UZEX",
              "TG: PR Media Group (запросы клиентов)"):
        assert s not in R._SKIP_SOURCES, s


# ── формат ────────────────────────────────────────────────────────────────────

def test_price_is_printed_with_spaces():
    assert R._fmt(1_225_574_400) == "1 225 574 400"
    assert R._fmt(None) == "0"


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
