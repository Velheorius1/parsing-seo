"""HTML adapter — scrapes tenders from HTML pages using BeautifulSoup."""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from crawler.adapters.base import BaseAdapter
from crawler.core.models import HtmlSelectors, RawTender, SourceConfig

logger = logging.getLogger(__name__)


def _safe_str(value: Any) -> str:
    """Convert value to string, return empty string for None."""
    if value is None:
        return ""
    return str(value).strip()


class HtmlAdapter(BaseAdapter):
    """Adapter for HTML scraping sources (httpx + BeautifulSoup)."""

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        if config.html_selectors is None:
            raise ValueError(
                "html_selectors required for HTML adapter (source: %s)" % config.id
            )

    async def _fetch_items(self) -> List[RawTender]:
        """Fetch and parse HTML pages."""
        cfg = self.config
        selectors = cfg.html_selectors
        if selectors is None:
            return []

        all_items = []  # type: List[RawTender]

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout, connect=10.0),
            headers=cfg.headers,
            follow_redirects=True,
        ) as client:
            html = await self._fetch_page(client, cfg.url)
            if not html:
                return []

            items = self._parse_page(html, cfg.url)
            all_items.extend(items)

            # Handle pagination via next_page selector
            if selectors.next_page:
                current_url = cfg.url
                for _page in range(9):  # max 10 pages total
                    soup = BeautifulSoup(html, "html.parser")
                    next_el = soup.select_one(selectors.next_page)
                    if next_el is None:
                        break

                    next_href = next_el.get("href")
                    if not next_href:
                        break

                    next_url = urljoin(current_url, str(next_href))
                    if next_url == current_url:
                        break

                    await self.rate_limit()
                    html = await self._fetch_page(client, next_url)
                    if not html:
                        break

                    page_items = self._parse_page(html, next_url)
                    if not page_items:
                        break

                    all_items.extend(page_items)
                    current_url = next_url

        return all_items

    async def _fetch_page(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[str]:
        """Fetch a single HTML page. Returns HTML string or None."""
        cfg = self.config
        await self.rate_limit()

        try:
            if cfg.method.upper() == "POST":
                resp = await client.post(url, json=cfg.body)
            else:
                resp = await client.get(url, params=cfg.params)

            resp.raise_for_status()
            text = resp.text
            if not text or len(text) < 50:
                return None
            return text
        except Exception as exc:
            logger.warning("[%s] Failed to fetch %s: %s", cfg.name, url, str(exc))
            return None

    def _parse_page(self, html: str, page_url: str) -> List[RawTender]:
        """Parse one HTML page and extract tender items."""
        cfg = self.config
        selectors = cfg.html_selectors
        if selectors is None:
            return []

        soup = BeautifulSoup(html, "html.parser")
        containers = soup.select(selectors.container)

        if not containers:
            logger.debug(
                "[%s] No containers found with selector: %s",
                cfg.name,
                selectors.container,
            )
            return []

        results = []  # type: List[RawTender]
        for idx, container in enumerate(containers):
            try:
                tender = self._parse_container(container, page_url, idx)
                if tender is not None:
                    results.append(tender)
            except Exception as exc:
                logger.debug(
                    "[%s] Skipping container %d: %s", cfg.name, idx, str(exc)
                )

        return results

    def _parse_container(
        self, container: Tag, page_url: str, idx: int
    ) -> Optional[RawTender]:
        """Parse a single container element into a RawTender."""
        cfg = self.config
        selectors = cfg.html_selectors
        if selectors is None:
            return None

        # Extract title
        title = self._extract_field(container, selectors.title)
        if not title or len(title) < 3:
            return None

        # Extract optional fields
        organization = self._extract_field(container, selectors.organization) if selectors.organization else ""
        if not organization:
            organization = cfg.name

        deadline = self._extract_field(container, selectors.deadline) if selectors.deadline else None
        if deadline == "":
            deadline = None

        price_str = self._extract_field(container, selectors.price) if selectors.price else None
        price = None  # type: Optional[float]
        if price_str:
            # Try to extract number from price string
            cleaned = re.sub(r"[^\d.,]", "", price_str)
            cleaned = cleaned.replace(",", ".")
            try:
                price = float(cleaned)
            except (ValueError, TypeError):
                pass

        # Extract link
        link = ""
        if selectors.link:
            link = self._extract_field(container, selectors.link)

        # Build source URL
        source_url = ""
        if link:
            if cfg.field_map.source_url_template:
                source_url = cfg.field_map.source_url_template.replace(
                    "{link}", link
                ).replace(
                    "{id}", link
                )
            elif link.startswith("http"):
                source_url = link
            else:
                source_url = urljoin(page_url, link)

        # External ID: from link or use index
        ext_id = ""
        if link:
            # Try to extract ID from link
            id_match = re.search(r"(\d+)", link)
            if id_match:
                ext_id = id_match.group(1)
            else:
                ext_id = link.strip("/").split("/")[-1] if "/" in link else link
        if not ext_id:
            ext_id = str(idx)

        tender_id = "%s-%s" % (cfg.id_prefix, ext_id)

        # Country filter (e.g. UNDP filtering for UZB)
        if cfg.country_filter:
            org_text = organization.upper()
            title_text = title.upper()
            # Check if country code appears in organization or full container text
            container_text = container.get_text().upper()
            if cfg.country_filter.upper() not in container_text:
                return None

        # Search text
        search_parts = [title]
        if organization:
            search_parts.append(organization)
        search_text = " ".join(search_parts)[:2000]

        return RawTender(
            id=tender_id,
            external_id=ext_id,
            title=title,
            organization=organization,
            price=price,
            currency=cfg.field_map.currency if cfg.field_map.currency else "USD",
            deadline=deadline,
            date_start=None,
            date_end=deadline,
            region="",
            categories=[],
            source=cfg.name,
            source_url=source_url,
            status="active",
            search_text=search_text,
        )

    def _extract_field(self, container: Tag, selector: str) -> str:
        """Extract text from a container using a CSS selector.

        Special syntax:
        - "@attr" at end of selector means extract that attribute
        - "tag@attr" means select tag, then get attr
        - "time[datetime]@datetime" means select time[datetime], get datetime attr
        """
        if not selector:
            return ""

        # Check for attribute extraction: selector@attr
        attr_name = None  # type: Optional[str]
        if "@" in selector:
            # Handle cases like "@href" (attr of container itself)
            # and ".class@href" (select child, get attr)
            parts = selector.rsplit("@", 1)
            if parts[0]:
                selector = parts[0]
                attr_name = parts[1]
            else:
                # Selector is just "@attr" — extract from container
                attr_name = parts[1]
                val = container.get(attr_name)
                return _safe_str(val)

        # Select element
        el = container.select_one(selector)
        if el is None:
            return ""

        if attr_name:
            val = el.get(attr_name)
            return _safe_str(val)

        return el.get_text(strip=True)
