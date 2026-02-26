"""Base adapter ABC — all adapter types inherit from this."""

import abc
import logging
from typing import List
from urllib.parse import urlparse

from crawler.core.models import RawTender, SourceConfig
from crawler.core.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class BaseAdapter(abc.ABC):
    """Abstract base for all tender source adapters.

    Subclasses implement _fetch_items(). The public fetch() wraps it
    with error handling so adapters never raise — they return [].
    """

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.domain = urlparse(config.url).netloc or config.id
        rate_limiter.configure(self.domain, config.rate_limit)

    async def fetch(self) -> List[RawTender]:
        """Fetch tenders from this source. Never raises — returns [] on error."""
        try:
            items = await self._fetch_items()
            logger.info(
                "[%s] Fetched %d items", self.config.name, len(items)
            )
            return items
        except Exception as exc:
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
