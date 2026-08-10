"""Unit test for db.query_with_retry — the shared transient-timeout retry (2026-07-21).

Locks the contract the 57014 fix depends on: retry a blocking Supabase call a few
times with backoff, return the first success, and re-raise the last error only after
all attempts are spent (so each caller can then choose its stale-cache / WARN fallback).

db.py imports crawler.config.settings + crawler.core.models at module load (need deps
not present locally); query_with_retry itself uses neither, so we stub those modules.
time.sleep is stubbed so the failure path doesn't actually sleep.

Run: python3 -m crawler.tests.test_query_retry   (exit 1 on any failure)
"""
import sys
import time
import types

from crawler.tests._stubs import install_stub


def _load():
    install_stub("crawler.config.settings",
                 settings=types.SimpleNamespace(supabase_url="x",
                                                supabase_service_role_key="y"))
    install_stub("crawler.core.models", RawTender=object)

    # ЗДЕСЬ НУЖЕН НАСТОЯЩИЙ db.py, а в общем прогоне на его месте лежит заглушка
    # соседа: test_mute_resilience подменяет crawler.core.db, чтобы не тянуть
    # клиент при импорте feedback.py, и в той заглушке есть только _get_client.
    # Отсюда «ImportError: cannot import name query_with_retry ... (unknown
    # location)» — модуль ЦЕЛИКОМ выпадал из общего прогона, обрывая сбор тестов,
    # а вместе с ним выпадала проверка контракта ретраев, на котором держится вся
    # защита от 57014. Поэтому грузим файл по пути, минуя sys.modules.
    mod = sys.modules.get("crawler.core.db")
    if mod is not None and getattr(mod, "query_with_retry", None) is None:
        import importlib.util
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "core", "db.py")
        spec = importlib.util.spec_from_file_location("crawler.core._db_for_test", path)
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)
        return real.query_with_retry

    from crawler.core.db import query_with_retry
    return query_with_retry


_SLEEPS = []
time.sleep = lambda s: _SLEEPS.append(s)  # never actually sleep in tests
query_with_retry = _load()


class _Boom(Exception):
    pass


def test_returns_on_first_success():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        return "ok"
    assert query_with_retry(fn, attempts=3) == "ok"
    assert calls["n"] == 1, calls  # no needless retries on success


def test_succeeds_after_transient_failures():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom("57014")
        return "recovered"
    assert query_with_retry(fn, attempts=3) == "recovered"
    assert calls["n"] == 3, calls


def test_reraises_last_error_after_all_attempts():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise _Boom("attempt %d" % calls["n"])
    raised = None
    try:
        query_with_retry(fn, attempts=3)
    except _Boom as e:
        raised = str(e)
    assert calls["n"] == 3, calls           # exactly `attempts` tries
    assert raised == "attempt 3", raised     # the LAST error, not the first


def test_backoff_between_but_not_after_last():
    _SLEEPS.clear()
    def fn():
        raise _Boom("x")
    try:
        query_with_retry(fn, attempts=3)
    except _Boom:
        pass
    # 3 attempts → sleeps only between them (2), never after the final failure
    assert _SLEEPS == [0.4, 0.8], _SLEEPS


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, "-", str(e)[:120])
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
