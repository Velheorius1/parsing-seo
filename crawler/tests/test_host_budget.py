"""Общая очередь запросов к бэкенду XT-Xarid на все процессы (10.10.2026).

ЧТО БЫЛО. С 07.10 площадка пускает с IP нашего VPS один запрос в минуту на
оба домена (api.xt-xarid.uz и api.hayotbirja.uz — один бэкенд). Ограничитель
внутри процесса считал домены порознь и не знал про соседние процессы: сторож
наших лотов, краул встречных аукционов и основной краул стартовали в одну
секунду, проходил один запрос из пачки, остальные получали 429. Шесть
источников трое суток отдавали 0 строк.

Здесь держится: оба домена (и их SPA-хосты) — одна очередь; слоты разнесены
на интервал; отказ по `max_wait` очередь не трогает; 429 отодвигает следующий
слот; конкурирующие процессы получают разные слоты.

Run: python3 -m crawler.tests.test_host_budget   (exit 1 on any failure)
"""
import multiprocessing
import os
import sys
import tempfile

from crawler.core import host_budget as hb

KEY = hb.XT_BACKEND


def _tmp():
    return tempfile.mkdtemp(prefix="host-budget-")


def test_both_domains_share_one_backend():
    assert hb.backend_for("https://api.xt-xarid.uz/rpc") == KEY
    assert hb.backend_for("https://api.hayotbirja.uz/rpc") == KEY
    assert hb.backend_for("https://xt-xarid.uz/procedure/tender") == KEY
    assert hb.backend_for("https://www.hayotbirja.uz/procedure/tender") == KEY
    assert hb.backend_for("https://api.xt-xarid.uz:443/urpc") == KEY


def test_other_hosts_are_not_queued():
    assert hb.backend_for("https://apietender.uzex.uz/api/common/DealsList") is None
    assert hb.backend_for("") is None
    assert hb.backend_for(None) is None


def test_slots_are_spaced_by_interval():
    d = _tmp()
    step = hb.interval_for(KEY)
    s1 = hb.reserve(KEY, now=1000.0, state_dir=d)
    s2 = hb.reserve(KEY, now=1000.0, state_dir=d)
    s3 = hb.reserve(KEY, now=1001.0, state_dir=d)
    assert s1 == 1000.0
    assert s2 == 1000.0 + step
    assert s3 == 1000.0 + 2 * step


def test_idle_queue_gives_slot_right_away():
    d = _tmp()
    hb.reserve(KEY, now=1000.0, state_dir=d)
    assert hb.reserve(KEY, now=5000.0, state_dir=d) == 5000.0


def test_too_long_wait_does_not_take_a_slot():
    d = _tmp()
    step = hb.interval_for(KEY)
    hb.reserve(KEY, now=1000.0, state_dir=d)
    assert hb.reserve(KEY, now=1000.0, max_wait=step - 1, state_dir=d) is None
    # отказ ничего не занял: следующий получит тот же слот
    assert hb.reserve(KEY, now=1000.0, state_dir=d) == 1000.0 + step


def test_penalize_pushes_next_slot_a_full_interval():
    d = _tmp()
    step = hb.interval_for(KEY)
    hb.reserve(KEY, now=1000.0, state_dir=d)
    hb.penalize(KEY, now=1030.0, state_dir=d)
    assert hb.reserve(KEY, now=1030.0, state_dir=d) == 1030.0 + step


def test_penalize_never_pulls_the_queue_closer():
    d = _tmp()
    step = hb.interval_for(KEY)
    for _ in range(5):
        hb.reserve(KEY, now=1000.0, state_dir=d)
    hb.penalize(KEY, now=1000.0, state_dir=d)
    assert hb.reserve(KEY, now=1000.0, state_dir=d) == 1000.0 + 5 * step


def test_corrupt_state_file_means_empty_queue():
    d = _tmp()
    with open(hb._state_path(KEY, d), "w") as f:
        f.write("{not json")
    assert hb.reserve(KEY, now=1000.0, state_dir=d) == 1000.0


def test_interval_is_at_least_the_measured_minute():
    # 10.10: 429 через 48 с после успеха, 200 через 61 с.
    old = os.environ.pop("PARSING_XT_INTERVAL_S", None)
    try:
        assert hb.interval_for(KEY) >= 61
    finally:
        if old is not None:
            os.environ["PARSING_XT_INTERVAL_S"] = old


def test_interval_env_override():
    old = os.environ.get("PARSING_XT_INTERVAL_S")
    os.environ["PARSING_XT_INTERVAL_S"] = "90"
    try:
        assert hb.interval_for(KEY) == 90.0
        os.environ["PARSING_XT_INTERVAL_S"] = "abc"
        assert hb.interval_for(KEY) == 62.0
    finally:
        if old is None:
            os.environ.pop("PARSING_XT_INTERVAL_S", None)
        else:
            os.environ["PARSING_XT_INTERVAL_S"] = old


def _reserve_in_child(d):
    return hb.reserve(KEY, now=1000.0, state_dir=d)


def test_processes_get_distinct_slots():
    # Ради этого всё и затевалось: соседние процессы больше не стартуют в одну секунду.
    d = _tmp()
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(6) as pool:
        slots = sorted(pool.map(_reserve_in_child, [d] * 12))
    step = hb.interval_for(KEY)
    assert slots == [1000.0 + i * step for i in range(12)], slots


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
