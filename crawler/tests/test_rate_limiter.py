"""Rate limiter не создаёт asyncio-объектов вне цикла событий (24.09.2026).

ЧТО БЫЛО. `RateLimiter.configure` создавал `asyncio.Lock()` и зовётся из
конструктора любого адаптера — то есть вне цикла. На Python 3.9 замок при
создании берёт текущий цикл потока, а `asyncio.run()` на выходе этот цикл
снимает. Итог: адаптер, созданный после любого `asyncio.run()` в том же потоке,
падал с «There is no current event loop in thread 'MainThread'».

В наборе это выглядело как порядок-зависимые падения: test_jsonrpc_outcome_fields
краснел в общем прогоне и зеленел в одиночку, а test_detail_text_persistence —
наоборот, падал в одиночку (соседний тест того же файла зовёт asyncio.run) и
проходил в общем, потому что кто-то между ними случайно оставлял цикл. То есть
второй был ложно-зелёным. Прод на 3.12, там замок при создании к циклу не
привязан, поэтому живой краул этого не видел.

Run: python3 -m crawler.tests.test_rate_limiter   (exit 1 on any failure)
"""
import asyncio
import sys

from crawler.core.rate_limiter import RateLimiter


def test_configure_after_asyncio_run_does_not_raise():
    # Ровно та последовательность, что роняла тесты адаптеров на 3.9.
    asyncio.run(asyncio.sleep(0))
    rl = RateLimiter()
    rl.configure("example.test", 2.0)


def test_configure_creates_no_lock():
    # Замок — забота acquire(), внутри цикла; configure остаётся чистой записью.
    rl = RateLimiter()
    rl.configure("example.test", 2.0)
    assert "example.test" not in rl._locks


def test_acquire_after_configure_outside_loop_works():
    asyncio.run(asyncio.sleep(0))
    rl = RateLimiter()
    rl.configure("example.test", 50.0)
    asyncio.run(rl.acquire("example.test"))
    assert "example.test" in rl._locks


def test_acquire_without_configure_uses_default():
    rl = RateLimiter()
    asyncio.run(rl.acquire("unconfigured.test"))
    assert rl._intervals["unconfigured.test"] == 1.0 / 2.0


def test_reconfigure_keeps_last_call_and_updates_interval():
    rl = RateLimiter()
    rl.configure("example.test", 2.0)
    rl._last_call["example.test"] = 123.0
    rl.configure("example.test", 4.0)
    assert rl._last_call["example.test"] == 123.0
    assert rl._intervals["example.test"] == 0.25


def test_concurrent_first_acquire_makes_one_lock():
    rl = RateLimiter()
    rl.configure("example.test", 1000.0)
    seen = []

    async def _one():
        await rl.acquire("example.test")
        seen.append(id(rl._locks["example.test"]))

    async def _many():
        await asyncio.gather(*[_one() for _ in range(5)])

    asyncio.run(_many())
    assert len(set(seen)) == 1, seen


def test_interval_is_still_enforced():
    # Ленивый замок не должен съесть саму паузу между запросами.
    rl = RateLimiter()
    rl.configure("slow.test", 10.0)  # 0.1 с между запросами
    import time as _t

    async def _two():
        await rl.acquire("slow.test")
        t0 = _t.monotonic()
        await rl.acquire("slow.test")
        return _t.monotonic() - t0

    gap = asyncio.run(_two())
    assert gap >= 0.08, gap


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, "-", type(e).__name__, str(e)[:140])
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
