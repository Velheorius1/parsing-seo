"""Gold standard: Python crawler adapter pattern.

All adapters inherit from BaseAdapter and implement _fetch_items().
Never raise — return [] on error (base class handles this).
"""

import logging
from typing import List

from crawler.adapters.base import BaseAdapter
from crawler.core.models import RawTender, SourceConfig

logger = logging.getLogger(__name__)


class ExampleAdapter(BaseAdapter):
    """One-line docstring: what this adapter does."""

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        # Validate required config fields here
        if not config.some_field:
            raise ValueError("some_field required (source: %s)" % config.id)

    async def _fetch_items(self) -> List[RawTender]:
        """Fetch tenders. May raise — base class catches and returns []."""
        await self.rate_limit()  # Always rate-limit before HTTP calls
        # ... fetch logic ...
        return [
            RawTender(
                id="%s-%s" % (self.config.id_prefix, ext_id),
                external_id=ext_id,
                title=title,
                organization=org,
                source=self.config.name,
                source_url=url,
                search_text=search_text,
            )
        ]
