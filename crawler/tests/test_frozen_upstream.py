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


def _load():
    name = "crawler.auth.session_store"
    if name not in sys.modules:
        m = types.ModuleType(name)
        m.session_store = types.SimpleNamespace(get_setting=lambda k: None,
                                                set_setting=lambda k, v: True)
        sys.modules[name] = m
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


class _FakeClient(object):
    """Минимальный дубль supabase-клиента: отдаёт max(created_at) по источнику.

    `created` — {источник: сколько дней назад появилась последняя новая строка};
    источник со значением None роняет запрос (проверяем, что это не читается
    как «свежий»).
    """

    def __init__(self, created):
        self._created = created
        self._src = None

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, _col, val):
        self._src = val
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        val = self._created.get(self._src, 0)
        if val is None:
            raise RuntimeError("statement timeout (57014)")
        return types.SimpleNamespace(data=[{"created_at": _iso(val)}])


def _run(rows, created, silent_names=None):
    orig = W._supabase
    W._supabase = lambda: _FakeClient(created)
    try:
        return W._frozen_sources(rows, silent_names or set())
    finally:
        W._supabase = orig


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


def test_tiny_source_is_ignored():
    assert _run([_row("A", cnt=W.MIN_ROWS - 1)], {"A": 200}) == []


def test_failed_query_is_not_treated_as_healthy():
    """Упавший запрос обязан выпасть из выдачи, а не притвориться свежим."""
    got = _run([_row("A"), _row("B")], {"A": None, "B": 200})
    assert [g["source"] for g in got] == ["B"], got


def test_sorted_by_staleness():
    got = _run([_row("A"), _row("B"), _row("C")],
               {"A": 30, "B": 150, "C": 60})
    assert [g["source"] for g in got] == ["B", "C", "A"], got


def test_row_shape_has_what_the_message_needs():
    got = _run([_row("A", cnt=993)], {"A": 100})[0]
    assert set(got) == {"source", "cnt", "days", "last_new"}
    assert got["cnt"] == 993 and len(got["last_new"]) == 10


def test_disabled_frozen_plans_source_is_allowlisted():
    """Фетчер отключён 05.08 — сторож не должен звенеть про него через неделю."""
    assert "Cooperation.uz Закупочные планы" in W.KNOWN_RETIRED


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
