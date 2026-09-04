"""Вес источника как часть тревоги (05.09).

ИЗ ЧЕГО ВЫРОСЛО. 29.08 резидентный прокси упёрся в 402, и вместе с ним встали
Cooperation.uz Лоты и UZEX Предквалификации — источники №2 и №3 по алертам,
43% недельного потока. Сигналы шли исправно: proxy_health_check писал «IPRoyal
402» каждые 12 часов, healthcheck — «token.cooperation EXPIRED» каждые пять.
Около 25 сообщений за 3,5 дня, и ни одно не сказало, ЧТО за этим стоит:
сторожа знали про хосты и токены, но не про вес источника. Простой заметили
руками на четвёртый день.

Здесь закреплено: вес считается по АЛЕРТАМ (сколько потеряем), тяжёлый молчун
даёт FAIL с долей потока, а не WARN про подсистему, и порог — два пропущенных
прогона, а не штатный разрыв между ними.

Run: python3 -m crawler.tests.test_source_weights   (exit 1 on any failure)
"""
import contextlib
import sys
import types

from crawler.tests._stubs import install_settings_stub, install_stub

install_settings_stub()

from crawler.core import source_health as SH  # noqa: E402


# ── impact_line ─────────────────────────────────────────────────────────────

def test_impact_line_names_sources_and_total_share():
    line = SH.impact_line({"Cooperation.uz Лоты": {"alerts": 53, "pct": 24.0},
                           "UZEX Предквалификации": {"alerts": 38, "pct": 17.0}},
                          ["Cooperation.uz Лоты", "UZEX Предквалификации"])
    assert "Cooperation.uz Лоты (24.0%)" in line
    assert "41% алертов за 30 дней" in line


def test_impact_line_skips_unknown_sources():
    line = SH.impact_line({"A": {"alerts": 1, "pct": 9.0}}, ["A", "нет такого"])
    assert "нет такого" not in line and "A (9.0%)" in line


def test_impact_line_is_empty_when_nothing_known():
    assert SH.impact_line({}, ["A"]) == ""


# ── source_weights ──────────────────────────────────────────────────────────

def _fake_iter(pages):
    def _iter(table, select, filters=None, **kw):
        for p in pages:
            yield p
    return _iter


@contextlib.contextmanager
def _with_rows(rows):
    """Подменить пагинатор и ВЕРНУТЬ как было.

    `install_stub` идемпотентен: если `crawler.core.db` уже импортирован
    по-настоящему, он отдаёт настоящий модуль — присваивание без отката
    оставило бы фальшивый `iter_rows` всему сьюту. Ровно так четыре теста
    scout полгода падали «на ровном месте» (см. test_scout_store_roundtrip).
    """
    db = install_stub("crawler.core.db")
    had = hasattr(db, "iter_rows")
    orig = getattr(db, "iter_rows", None)
    db.iter_rows = _fake_iter([rows])
    try:
        yield db
    finally:
        if had:
            db.iter_rows = orig
        else:
            delattr(db, "iter_rows")


@contextlib.contextmanager
def _with_weights(weights, heavy):
    """Подменить расчёт весов на время одного теста, потом вернуть настоящий."""
    orig = SH.source_weights
    SH.source_weights = lambda *a, **k: {"weights": weights, "total": 100, "heavy": heavy}
    try:
        yield
    finally:
        SH.source_weights = orig


def test_weights_are_shares_of_alerts_not_of_collected_rows():
    with _with_rows([{"source": "A"}] * 30 + [{"source": "B"}] * 10):
        rep = SH.source_weights()
    assert rep["total"] == 40
    assert rep["weights"]["A"]["pct"] == 75.0
    assert rep["weights"]["B"]["alerts"] == 10


def test_heavy_is_sorted_and_cut_by_threshold():
    with _with_rows([{"source": "big"}] * 50 + [{"source": "mid"}] * 40
                    + [{"source": "tiny"}] * 2):
        rep = SH.source_weights(min_share=5.0)
    assert rep["heavy"] == ["big", "mid"], rep["heavy"]
    assert "tiny" in rep["weights"], "малый источник не исчезает из весов"


def test_empty_period_does_not_divide_by_zero():
    with _with_rows([]):
        rep = SH.source_weights()
    assert rep == {"weights": {}, "total": 0, "heavy": []}


def test_rows_without_source_are_ignored():
    with _with_rows([{"source": None}, {"source": "A"}]):
        rep = SH.source_weights()
    assert rep["total"] == 1 and rep["weights"]["A"]["pct"] == 100.0


def test_muted_sources_are_out_of_weights_and_denominator():
    """Замер на живой базе 05.09: в «тяжёлые» попали два е-магазина UZEX (8.3%
    и 6.5%), пуши которых выключены решением 22.08 — 30-дневное окно ещё
    захватывает доотключённую историю. Их остановка подняла бы ложный FAIL."""
    orig = SH._sources_we_never_push
    SH._sources_we_never_push = lambda: {"UZEX Э-магазин бумага и изделия"}
    try:
        with _with_rows([{"source": "UZEX Э-магазин бумага и изделия"}] * 60
                        + [{"source": "Живой"}] * 40):
            rep = SH.source_weights()
    finally:
        SH._sources_we_never_push = orig
    assert rep["total"] == 40, "заглушённые попали в знаменатель"
    assert "UZEX Э-магазин бумага и изделия" not in rep["weights"]
    assert rep["weights"]["Живой"]["pct"] == 100.0
    assert rep["heavy"] == ["Живой"]


# ── healthcheck: FAIL с ценой простоя ───────────────────────────────────────

class _Res(object):
    def __init__(self, data):
        self.data = data


class _Table(object):
    def __init__(self, last_by_source):
        self._last = last_by_source
        self._src = None

    def select(self, *a, **k):
        return self

    def eq(self, _col, val):
        self._src = val
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        val = self._last.get(self._src)
        return _Res([{"collected_at": val}] if val else [])


class _Client(object):
    def __init__(self, last_by_source):
        self._last = last_by_source

    def table(self, _name):
        return _Table(self._last)


def _hc(last_by_source):
    import crawler.scripts.healthcheck as H
    hc = H.HealthCheck()
    hc.client = _Client(last_by_source)
    return H, hc


def _verdict(hc):
    return [x for x in hc.results if x["component"] == "sources.heavy"][0]


def _iso(hours_ago):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_stale_heavy_source_fails_with_its_share():
    weights = {"Cooperation.uz Лоты": {"alerts": 53, "pct": 24.0},
               "UZEX Предквалификации": {"alerts": 38, "pct": 17.0}}
    H, hc = _hc({"Cooperation.uz Лоты": _iso(96), "UZEX Предквалификации": _iso(80)})
    with _with_weights(weights, list(weights)):
        hc.check_heavy_sources()
    r = _verdict(hc)
    assert r["status"] == "fail", r
    assert "41%" in r["message"], r["message"]
    assert "Cooperation.uz Лоты молчит 96ч" in r["message"]


def test_fresh_heavy_source_is_ok_at_the_normal_gap():
    """12 часов — штатный разрыв между прогонами прокси-фетча, не поломка."""
    weights = {"Cooperation.uz Лоты": {"alerts": 53, "pct": 24.0}}
    H, hc = _hc({"Cooperation.uz Лоты": _iso(12)})
    with _with_weights(weights, list(weights)):
        hc.check_heavy_sources()
    r = _verdict(hc)
    assert r["status"] == "ok", r


def test_source_without_any_rows_counts_as_stale():
    weights = {"Новый тяжёлый": {"alerts": 20, "pct": 20.0}}
    H, hc = _hc({})
    with _with_weights(weights, list(weights)):
        hc.check_heavy_sources()
    r = _verdict(hc)
    assert r["status"] == "fail" and "всегда" in r["message"]


def test_heavy_check_is_supabase_dependent():
    """Иначе падение Supabase породит ложный FAIL про «встал источник»."""
    import crawler.scripts.healthcheck as H
    assert "sources.heavy" in H.SUPABASE_DEPENDENT_COMPONENTS


def test_stubs_do_not_leak_out_of_a_test():
    """Пин на собственную гигиену: после тестов настоящие функции на месте."""
    import crawler.core.db as real_db
    with _with_rows([{"source": "A"}]):
        pass
    assert SH.source_weights.__module__ == SH.__name__, "подменённые веса утекли"
    assert getattr(real_db, "iter_rows", None) is None or callable(real_db.iter_rows)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
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

