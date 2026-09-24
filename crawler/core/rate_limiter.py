"""Per-domain async rate limiter using asyncio.Semaphore + sleep."""

import asyncio
import time
from typing import Dict


class RateLimiter:
    """Simple token-bucket rate limiter per domain.

    Each domain gets its own semaphore (concurrency=1) and enforces
    a minimum interval between requests (1 / rate_limit seconds).
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_call: Dict[str, float] = {}
        self._intervals: Dict[str, float] = {}

    def configure(self, domain: str, rate_limit: float) -> None:
        """Set rate limit for a domain (requests per second).

        Без asyncio намеренно: зовётся из конструктора адаптера, то есть часто
        ВНЕ цикла событий. Раньше здесь создавался asyncio.Lock(), а на Python
        3.9 такой замок при создании берёт текущий цикл потока — и падает с
        «There is no current event loop», если в этом потоке уже отработал
        любой asyncio.run() (тот на выходе снимает цикл). Так падали тесты,
        создающие адаптер после соседнего теста с asyncio.run; а в живом коде
        3.9 замок, созданный вне цикла, привязывался бы не к тому циклу.
        Прод на 3.12, там замок к циклу при создании не привязан, — поэтому
        сам прод этого не видел. Замок теперь создаётся в acquire().
        """
        self._last_call.setdefault(domain, 0.0)
        self._intervals[domain] = 1.0 / max(rate_limit, 0.1)

    async def acquire(self, domain: str) -> None:
        """Wait until it's safe to make a request to this domain."""
        if domain not in self._intervals:
            self.configure(domain, 2.0)

        lock = self._locks.get(domain)
        if lock is None:
            # Внутри работающего цикла; между get и присваиванием нет await,
            # так что две корутины не создадут два замка на один домен.
            lock = self._locks[domain] = asyncio.Lock()

        async with lock:
            now = time.monotonic()
            interval = self._intervals.get(domain, 0.5)
            elapsed = now - self._last_call[domain]
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
            self._last_call[domain] = time.monotonic()


# Global singleton
rate_limiter = RateLimiter()
