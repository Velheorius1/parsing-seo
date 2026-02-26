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
        """Set rate limit for a domain (requests per second)."""
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
            self._last_call[domain] = 0.0
        self._intervals[domain] = 1.0 / max(rate_limit, 0.1)

    async def acquire(self, domain: str) -> None:
        """Wait until it's safe to make a request to this domain."""
        if domain not in self._locks:
            self.configure(domain, 2.0)

        async with self._locks[domain]:
            now = time.monotonic()
            interval = self._intervals.get(domain, 0.5)
            elapsed = now - self._last_call[domain]
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
            self._last_call[domain] = time.monotonic()


# Global singleton
rate_limiter = RateLimiter()
