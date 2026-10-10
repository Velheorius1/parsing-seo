"""SPA adapter — uses Playwright to scrape JavaScript-rendered pages."""

import asyncio
import logging
from typing import List, Optional

from crawler.adapters.base import BaseAdapter
from crawler.config.settings import settings
from crawler.core.models import RawTender, SourceConfig

logger = logging.getLogger(__name__)

# Chromium обрывает переход с net::ERR_NETWORK_CHANGED, когда на хосте
# появляется или исчезает сетевой интерфейс. На VPS это каждые 5 минут:
# `run_erp_telegram.sh --process-sources` поднимает контейнер winch-bot через
# `docker compose run --rm`, он живёт ~4 с, и его veth рождается и умирает в
# :00:01-:00:05 — ровно когда основной прогон открывает первую SPA-страницу
# (10.10: ebirja-announcements в 06:00 и 08:00 подряд, 8 раз с 25.09; по
# секундам совпало с journalctl). Ошибка про хост, а не про площадку, поэтому
# загрузка страницы повторяется после паузы (см. _open); любая другая ошибка —
# сразу наверх.
_NETWORK_CHANGED = "ERR_NETWORK_CHANGED"
_GOTO_ATTEMPTS = 3
_GOTO_RETRY_PAUSE_S = 5.0


class SpaAdapter(BaseAdapter):
    """Fetch tenders from JavaScript SPAs using headless Chromium."""

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        if not config.html_selectors:
            raise ValueError(
                "SPA adapter requires html_selectors (source: %s)" % config.id
            )
        if not config.wait_selector:
            raise ValueError(
                "SPA adapter requires wait_selector (source: %s)" % config.id
            )

    async def _fetch_items(self) -> List[RawTender]:
        """Launch browser, navigate, wait for SPA render, extract tenders."""
        from playwright.async_api import async_playwright

        selectors = self.config.html_selectors
        assert selectors is not None  # checked in __init__

        tenders: List[RawTender] = []
        timeout_ms = self.config.timeout * 1000

        async with async_playwright() as pw:
            # Use residential proxy for geo-restricted sources
            proxy_cfg = None
            if self.config.use_proxy and settings.residential_proxy_url:
                # Parse proxy URL: http://user:pass@host:port
                proxy_url = settings.residential_proxy_url
                logger.info("[%s] Using residential proxy for Playwright", self.config.name)
                proxy_cfg = {"server": proxy_url}

            browser = await pw.chromium.launch(
                headless=True,
                proxy=proxy_cfg,
            )
            page = None
            try:
                page = await browser.new_page()
                # Переход и ожидание рендера SPA — с повтором на смену сети.
                await self._open(page, timeout_ms)
                # Small delay for remaining DOM elements to stabilize
                await page.wait_for_timeout(1000)

                # Extract from current page
                page_tenders = await self._extract_page(page)
                tenders.extend(page_tenders)

                # Handle pagination
                if selectors.next_page:
                    tenders = await self._paginate(
                        page, tenders, selectors.next_page, timeout_ms
                    )
            finally:
                if page:
                    await page.close()
                await browser.close()

        return tenders

    async def _open(self, page, timeout_ms):  # type: ignore[no-untyped-def]
        """goto + wait_for_selector с повтором на смену сети (см. _NETWORK_CHANGED).

        Смена сети бьёт по-разному: во время перехода — сам goto падает с
        ERR_NETWORK_CHANGED; после него — рвётся XHR, которым SPA тянет данные,
        таблица не рисуется, и ждём рендер до таймаута (A/B на проде 10.10:
        старт на :02 — оба адаптера упали на wait_for_selector, на :03 — на
        goto). Второй случай узнаём по `requestfailed` с тем же кодом в этой
        попытке; таймаут без такой улики — проблема площадки, не повторяем.
        """
        changed = []  # type: List[str]

        def on_failed(request):  # type: ignore[no-untyped-def]
            if _NETWORK_CHANGED in (request.failure or ""):
                changed.append(request.url)

        page.on("requestfailed", on_failed)
        try:
            for attempt in range(1, _GOTO_ATTEMPTS + 1):
                del changed[:]
                try:
                    await page.goto(
                        self.config.url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    await page.wait_for_selector(
                        self.config.wait_selector, timeout=timeout_ms
                    )
                    return
                except Exception as exc:
                    hit = _NETWORK_CHANGED in str(exc) or bool(changed)
                    if not hit or attempt == _GOTO_ATTEMPTS:
                        raise
                    logger.warning(
                        "[%s] %s — смена сети на хосте (%s), повтор %d/%d через %.0f с",
                        self.config.name, _NETWORK_CHANGED,
                        "goto" if _NETWORK_CHANGED in str(exc) else "запрос страницы",
                        attempt, _GOTO_ATTEMPTS - 1, _GOTO_RETRY_PAUSE_S,
                    )
                    await asyncio.sleep(_GOTO_RETRY_PAUSE_S)
        finally:
            page.remove_listener("requestfailed", on_failed)

    async def _extract_page(self, page) -> List[RawTender]:  # type: ignore[no-untyped-def]
        """Extract tender items from the current page DOM."""
        selectors = self.config.html_selectors
        assert selectors is not None

        items = await page.query_selector_all(selectors.container)
        tenders: List[RawTender] = []

        for idx, item in enumerate(items):
            try:
                tender = await self._parse_item(item, idx)
                if tender is not None:
                    tenders.append(tender)
            except Exception as exc:
                logger.debug(
                    "[%s] Failed to parse item %d: %s",
                    self.config.name,
                    idx,
                    str(exc),
                )

        return tenders

    async def _parse_item(self, item, idx: int) -> Optional[RawTender]:  # type: ignore[no-untyped-def]
        """Parse a single DOM element into a RawTender."""
        selectors = self.config.html_selectors
        assert selectors is not None

        title = await self._get_text(item, selectors.title)
        if not title:
            return None

        organization = ""
        if selectors.organization:
            organization = await self._get_text(item, selectors.organization) or ""

        price = None  # type: Optional[float]
        if selectors.price:
            price_text = await self._get_text(item, selectors.price)
            if price_text:
                price = self._parse_price(price_text)

        deadline = None  # type: Optional[str]
        if selectors.deadline:
            deadline = await self._get_text(item, selectors.deadline)

        source_url = ""
        if selectors.link:
            link_el = await item.query_selector(selectors.link)
            if link_el:
                href = await link_el.get_attribute("href")
                if href:
                    # Make absolute URL if relative
                    if href.startswith("/"):
                        from urllib.parse import urlparse

                        parsed = urlparse(self.config.url)
                        source_url = "%s://%s%s" % (
                            parsed.scheme,
                            parsed.netloc,
                            href,
                        )
                    elif href.startswith("http"):
                        source_url = href
                    else:
                        source_url = self.config.url.rstrip("/") + "/" + href

        # Extract external_id from URL if possible (e.g. /procedure/6680886/core → 6680886)
        external_id = str(idx)
        if source_url:
            import re

            id_match = re.search(r"/(\d{4,})", source_url)
            if id_match:
                external_id = id_match.group(1)
        tender_id = "%s-%s" % (self.config.id_prefix, external_id)

        search_text = " ".join(
            filter(None, [title, organization, deadline or ""])
        )

        return RawTender(
            id=tender_id,
            external_id=external_id,
            title=title.strip(),
            organization=organization.strip(),
            price=price,
            currency=self.config.field_map.currency or "UZS",
            deadline=deadline,
            source=self.config.name,
            source_url=source_url,
            search_text=search_text.lower(),
        )

    async def _paginate(
        self,
        page,  # type: ignore[no-untyped-def]
        tenders: List[RawTender],
        next_selector: str,
        timeout_ms: int,
        max_pages: int = 10,
    ) -> List[RawTender]:
        """Click through pagination and collect tenders from each page."""
        for page_num in range(2, max_pages + 1):
            await self.rate_limit()

            next_btn = await page.query_selector(next_selector)
            if not next_btn:
                break

            is_disabled = await next_btn.get_attribute("disabled")
            if is_disabled is not None:
                break

            try:
                await next_btn.click()
                await page.wait_for_selector(
                    self.config.wait_selector, timeout=timeout_ms
                )
                # Small delay for DOM to stabilize
                await page.wait_for_timeout(500)

                page_tenders = await self._extract_page(page)
                if not page_tenders:
                    break  # No more items
                tenders.extend(page_tenders)
                logger.debug(
                    "[%s] Page %d: %d items",
                    self.config.name,
                    page_num,
                    len(page_tenders),
                )
            except Exception as exc:
                logger.debug(
                    "[%s] Pagination stopped at page %d: %s",
                    self.config.name,
                    page_num,
                    str(exc),
                )
                break

        return tenders

    @staticmethod
    async def _get_text(element, selector: str) -> Optional[str]:  # type: ignore[no-untyped-def]
        """Get text content of a child element matching CSS selector."""
        el = await element.query_selector(selector)
        if el is None:
            return None
        text = await el.text_content()
        if text:
            return text.strip()
        return None

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """Try to extract a numeric price from text."""
        import re

        # Remove currency words and whitespace
        cleaned = re.sub(r"[^\d.,]", " ", text).strip()
        # Take the first number-like sequence
        match = re.search(r"[\d]+(?:[.,\s][\d]+)*", cleaned)
        if not match:
            return None
        num_str = match.group(0).replace(" ", "").replace(",", ".")
        # Handle cases like "1.234.567" (thousand separators with dots)
        parts = num_str.split(".")
        if len(parts) > 2:
            # Multiple dots = thousand separators
            num_str = "".join(parts)
        try:
            return float(num_str)
        except ValueError:
            return None
