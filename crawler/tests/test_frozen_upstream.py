"""Пины проверки «upstream заморожен» (05.08).

Дефект, ради которого она заведена: «Cooperation.uz Закупочные планы» полгода
собирались три раза в сутки и по `collected_at` выглядели живее всех живых,
хотя эндпоинт площадки стоит с 03.02.2026 и не отдал ни одной новой строки.
Первая проверка сторожа смотрит именно collected_at — и молчала.

Свойства, которые тут держатся:
  • источник со свежим collected_at и старым created_at — ЗАМОРОЖЕН;
  • свежий по обоим — молчим (иначе сторож превратится в шум);
  • тот, кто молчит целиком, во второй список не попадает (о нём уже сказано);
  • KNOWN_RETIRED и порог MIN_ROWS уважаются и здесь;
  • сбой запроса не читается как «всё в порядке» (капкан 57014 из
    metrics_tracker: упавший счётчик выглядел нулём).

Run: python3 -m crawler.tests.test_frozen_upstream   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta, timezone

from crawler.tests._stubs import install_stub


def _load():
    install_stub("crawler.auth.session_store",
                 session_store=types.SimpleNamespace(get_setting=lambda k: None,
                                                     set_setting=lambda k, v: True))
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(telegram_bot_token="",
                                           telegram_alert_chat_id="",
                                           supabase_url="", supabase_service_role_key="")
        sys.modules[cfg] = m
    import crawler.scripts.freshness_watchdog as W
    return W


W = _load()
NOW = datetime.now(timezone.utc)


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def _run(rows, created, silent_names=None):
    """`created` — {источник: сколько дней назад появилась последняя новая строка}.

    None означает, что RPC не отдал last_created для этого источника (так
    выглядит выдача, пока миграция 020 не применена).
    """
    enriched = []
    for r in rows:
        days_ago = created.get(r["source"], 0)
        r = dict(r)
        r["last_created"] = None if days_ago is None else _iso(days_ago)
        enriched.append(r)
    return W._frozen_sources(enriched, silent_names or set())


def _row(src, cnt=100, collected_days=0):
    return {"source": src, "cnt": cnt, "last_collected": _iso(collected_days)}


def test_fresh_collect_but_stale_insert_is_frozen():
    got = _run([_row("A")], {"A": W.FROZEN_DAYS + 40})
    assert [g["source"] for g in got] == ["A"], got
    assert got[0]["days"] >= W.FROZEN_DAYS


def test_fresh_on_both_dates_is_quiet():
    assert _run([_row("A")], {"A": 1}) == []


def test_just_under_threshold_is_quiet():
    """Порог 21д выбран, чтобы корпоративные площадки не шумели."""
    assert _run([_row("A")], {"A": W.FROZEN_DAYS - 1}) == []


def test_silent_source_is_not_reported_twice():
    """Про молчащий целиком сторож уже сказал первой проверкой."""
    assert _run([_row("A", collected_days=30)], {"A": 200}, silent_names={"A"}) == []


def test_source_silent_by_collected_at_is_left_to_first_check():
    got = _run([_row("A", collected_days=W.SILENCE_DAYS + 1)], {"A": 200})
    assert got == [], got


def test_known_retired_is_ignored():
    src = sorted(W.KNOWN_RETIRED)[0]
    assert _run([_row(src)], {src: 200}) == []


def test_small_source_is_still_judged():
    """Свой порог, ниже MIN_ROWS: дефект прячется как раз в мелких источниках.

    Saneg — 18 строк и 93 дня без единой новой; под общим MIN_ROWS=20 он
    выпадал из выдачи вместе с четырьмя такими же.
    """
    assert W.FROZEN_MIN_ROWS < W.MIN_ROWS
    got = _run([_row("Saneg", cnt=18)], {"Saneg": 93})
    assert [g["source"] for g in got] == ["Saneg"], got


def test_source_too_thin_to_judge_is_ignored():
    """2 строки за всю историю: «нового нет» неотличимо от «почти не публикуют»."""
    assert _run([_row("A", cnt=W.FROZEN_MIN_ROWS - 1)], {"A": 200}) == []


def test_missing_field_is_not_treated_as_healthy():
    """Нет last_created — источник выпадает из выдачи, а не «свежий»."""
    got = _run([_row("A"), _row("B")], {"A": None, "B": 200})
    assert [g["source"] for g in got] == ["B"], got


def test_migration_not_applied_yields_nothing_and_warns():
    """Пока RPC не отдаёт last_created, проверка обязана молчать целиком.

    Молчать — но не притворяться, что всё проверено: в лог уходит warning.
    Ровно на смешении «сканер не отработал» и «чисто» вставал gitleaks-хук.
    """
    seen = []
    orig = W.logger.warning
    W.logger.warning = lambda *a, **k: seen.append(a)
    try:
        got = _run([_row("A"), _row("B")], {"A": None, "B": None})
    finally:
        W.logger.warning = orig
    assert got == []
    assert seen and "020" in " ".join(str(x) for x in seen[0])


def test_sorted_by_staleness():
    got = _run([_row("A"), _row("B"), _row("C")],
               {"A": 30, "B": 150, "C": 60})
    assert [g["source"] for g in got] == ["B", "C", "A"], got


def test_row_shape_has_what_the_message_needs():
    got = _run([_row("A", cnt=993)], {"A": 100})[0]
    assert set(got) == {"source", "cnt", "days", "last_new"}
    assert got["cnt"] == 993 and len(got["last_new"]) == 10


def test_sources_retired_on_05_08_are_allowlisted():
    """Отключённые 05.08 — сторож не должен звенеть про них через неделю.

    Все три сняты осознанно: у планов заморожен upstream, у встречного
    аукциона external_id был порядковым номером строки, «Ebirja Аукционы»
    заменены источником с настоящим id лота.
    """
    for src in ("Cooperation.uz Закупочные планы",
                "E-Birja встречный аукцион (листинг)",
                "Ebirja Аукционы"):
        assert src in W.KNOWN_RETIRED, src


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
