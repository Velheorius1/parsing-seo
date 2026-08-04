"""Устойчивость сборщика метрик к statement timeout 57014 (04.08).

История дефекта в двух актах. Крон `0 0 * * * metrics_tracker --save --compare`
отрабатывал, а суточный снапшот не появлялся: исключение Supabase вылетало из
collect_metrics и валило main() целиком. Провал невидим — краул пишет в тот же
лог, и cron рапортует нормальный старт. Цена: недельная рутина 03.08 нашла в
слоте «эта неделя» снапшот недельной давности и посчитала `alerts_week: 0`.

  акт 1 (742807b, 03.08) — глубокий OFFSET в `_fetch_all` перерос timeout на
                           ~68k строк; починено уполовиниванием страницы
  акт 2 (этот тест)      — `_get_count` остался как был, и 04.08 00:00:12 снапшот
                           упал снова, теперь на COUNT(*) по всей `tenders`

Тесты держат обе половины и главное свойство: деградация НИКОГДА не молчит —
либо точное число, либо помеченная оценка, либо исключение. Тихого усечения нет.

metrics_tracker тянет supabase/httpx на импорте модуля — здесь не нужны, стабим.

Run: python3 -m crawler.tests.test_metrics_resilience   (exit 1 on any failure)
"""
import sys
import types


def _load():
    for name in ("supabase", "httpx"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.create_client = lambda *a, **k: None
            sys.modules[name] = mod
    from crawler.scripts import metrics_tracker
    return metrics_tracker


mt = _load()

TIMEOUT_ERR = ("{'message': 'canceling statement due to statement timeout', "
               "'code': '57014', 'hint': None, 'details': None}")


class _Result(object):
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _Query(object):
    """Заглушка постгрестового билдера: копит вызовы, отдаёт заранее заданное."""

    def __init__(self, table):
        self.table = table

    def select(self, select, count=None):
        self.table.last_count_mode = count
        return self

    def limit(self, n):
        return self

    def range(self, lo, hi):
        self.lo, self.hi = lo, hi
        return self

    def execute(self):
        return self.table.execute(self)


class _Table(object):
    def __init__(self, handler):
        self.handler = handler
        self.last_count_mode = None
        self.calls = []

    def execute(self, q):
        self.calls.append(getattr(self, "last_count_mode", None))
        return self.handler(self, q)


class _Client(object):
    def __init__(self, handler):
        self.tbl = _Table(handler)

    def table(self, name):
        return self.tbl

    # postgrest-like: client.table(x).select(...) -> _Query
    def __getattr__(self, item):
        raise AttributeError(item)


def _client(handler):
    c = _Client(handler)
    orig = c.table

    def table(name):
        t = orig(name)
        t._q = _Query(t)
        return _Shim(t)
    c.table = table
    return c


class _Shim(object):
    def __init__(self, table):
        self.table = table

    def select(self, select, count=None):
        self.table.last_count_mode = count
        return _Query(self.table)


# ── _get_count ───────────────────────────────────────────────────────────────

def test_exact_count_is_used_when_it_fits():
    def handler(tbl, q):
        return _Result(count=4242)
    n = mt._get_count(_client(handler), "tenders")
    assert n == 4242, n


def test_timeout_falls_back_to_estimate_instead_of_crashing():
    """Ровно тот сбой, что уронил снапшот 04.08 00:00."""
    def handler(tbl, q):
        if tbl.last_count_mode == "exact":
            raise Exception(TIMEOUT_ERR)
        return _Result(count=1_500_000)
    n = mt._get_count(_client(handler), "tenders")
    assert n == 1_500_000, n


def test_estimate_path_is_actually_the_planned_mode():
    seen = []

    def handler(tbl, q):
        seen.append(tbl.last_count_mode)
        if tbl.last_count_mode == "exact":
            raise Exception(TIMEOUT_ERR)
        return _Result(count=7)
    mt._get_count(_client(handler), "tenders")
    assert seen == ["exact", "planned"], seen


def test_non_timeout_errors_still_raise():
    """Права доступа или опечатка в таблице не должны маскироваться оценкой."""
    def handler(tbl, q):
        raise Exception("{'message': 'permission denied', 'code': '42501'}")
    try:
        mt._get_count(_client(handler), "tenders")
    except Exception as exc:
        assert "permission denied" in str(exc), exc
    else:
        raise AssertionError("исключение должно было пробросить наверх")


def test_estimate_failure_is_not_swallowed():
    def handler(tbl, q):
        raise Exception(TIMEOUT_ERR)
    try:
        mt._get_count(_client(handler), "tenders")
    except Exception:
        pass
    else:
        raise AssertionError("если и оценка не удалась — должно упасть, а не вернуть 0")


# ── _fetch_all (пин фикса 742807b, до сих пор без теста в репозитории) ────────

def test_fetch_all_returns_every_row_across_pages():
    rows = [{"i": i} for i in range(2300)]

    def handler(tbl, q):
        return _Result(data=rows[q.lo:q.hi + 1])
    got = mt._fetch_all(_client(handler), "tenders", "i")
    assert len(got) == 2300, len(got)
    assert got[0]["i"] == 0 and got[-1]["i"] == 2299


def test_fetch_all_halves_the_page_on_timeout_and_still_completes():
    rows = [{"i": i} for i in range(1500)]
    state = {"failed": 0}

    def handler(tbl, q):
        # Первый широкий запрос падает по таймауту, как на проде при offset=63000
        if q.hi - q.lo + 1 > 500 and state["failed"] < 1:
            state["failed"] += 1
            raise Exception(TIMEOUT_ERR)
        return _Result(data=rows[q.lo:q.hi + 1])
    got = mt._fetch_all(_client(handler), "tenders", "i")
    assert state["failed"] == 1, state
    assert len(got) == 1500, len(got)


def test_fetch_all_raises_rather_than_truncating_silently():
    def handler(tbl, q):
        raise Exception(TIMEOUT_ERR)
    try:
        mt._fetch_all(_client(handler), "tenders", "i")
    except Exception:
        pass
    else:
        raise AssertionError("вечно падающая страница обязана бросать, а не возвращать хвост")


if __name__ == "__main__":
    import time as _t
    _t.sleep = lambda *a, **k: None  # не спать на путях с backoff
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
