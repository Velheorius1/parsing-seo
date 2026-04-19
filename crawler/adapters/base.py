"""Base adapter ABC — all adapter types inherit from this."""

import abc
import logging
from typing import List, Optional
from urllib.parse import urlparse

from crawler.core.models import RawTender, SourceConfig
from crawler.core.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class BaseAdapter(abc.ABC):
    """Abstract base for all tender source adapters.

    Subclasses implement _fetch_items(). The public fetch() wraps it
    with error handling so adapters never raise — they return [].

    Outcome attributes (RISK-1 / task #6 — zero-result monitor):
        last_skipped_no_auth: True if adapter returned [] because the auth
            token was missing/invalid. Distinct from "API returned empty".
            Adapters set this inside _fetch_items() before returning [].
        last_error: str(exc)[:200] if _fetch_items() raised. None otherwise.
    Both are reset at the start of every fetch() call.
    """

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.domain = urlparse(config.url).netloc or config.id
        rate_limiter.configure(self.domain, config.rate_limit)
        self.last_skipped_no_auth = False  # type: bool
        self.last_error = None  # type: Optional[str]

    async def fetch(self) -> List[RawTender]:
        """Fetch tenders from this source. Never raises — returns [] on error."""
        self.last_skipped_no_auth = False
        self.last_error = None
        try:
            items = await self._fetch_items()
            logger.info(
                "[%s] Fetched %d items", self.config.name, len(items)
            )
            return items
        except Exception as exc:
            self.last_error = str(exc)[:200]
            logger.warning(
                "[%s] Error: %s", self.config.name, str(exc)
            )
            return []

    @abc.abstractmethod
    async def _fetch_items(self) -> List[RawTender]:
        """Implement actual fetching logic. May raise."""
        ...

    async def rate_limit(self) -> None:
        """Wait according to rate limiter for this source's domain."""
        await rate_limiter.acquire(self.domain)
