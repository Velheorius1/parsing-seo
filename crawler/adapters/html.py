"""HTML adapter — scrapes tenders from HTML pages using BeautifulSoup."""

import asyncio
import hashlib
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from crawler.adapters.base import BaseAdapter
from crawler.config.settings import settings
from crawler.core.models import HtmlSelectors, RawTender, SourceConfig

logger = logging.getLogger(__name__)


def _safe_str(value: Any) -> str:
    """Convert value to string, return empty string for None."""
    if value is None:
        return ""
    return str(value).strip()


# Системные наборы корневых сертификатов, по убыванию распространённости:
# Debian/Ubuntu (VPS), RHEL, Alpine.
_SYSTEM_CA_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)


def _ca_bundle(cfg: SourceConfig) -> Any:
    """Что подсунуть httpx как `verify`.

    По умолчанию — True, то есть certifi. `use_system_ca: true` в конфиге
    переключает на системное хранилище: у OSCE цепочка опирается на корень,
    которого в certifi нет, и источник молчал 28 дней с
    CERTIFICATE_VERIFY_FAILED. Проверку это НЕ выключает.

    Если системного файла нет (например, на Mac разработчика), честно
    возвращаемся к certifi и пишем предупреждение — молча делать вид, что
    настройка применилась, нельзя.
    """
    if not getattr(cfg, "use_system_ca", False):
        return True
    import os

    for path in _SYSTEM_CA_PATHS:
        if os.path.exists(path):
            return path
    logger.warning(
        "[%s] use_system_ca: системного набора корней не нашлось (%s) — "
        "остаёмся на certifi, ошибка проверки сертификата может повториться",
        cfg.name, ", ".join(_SYSTEM_CA_PATHS),
    )
    return True


_MONTHS_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES_EN = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "sept",
)
_EN_DEADLINE_RE = re.compile(
    r"(\d{1,2})[- ]([A-Za-z]{3,9})[- ](\d{4})"
    r"(?:\s+(\d{1,2}):(\d{2}))?"
    r"(?:\s*\(GMT\s*([+-]?)(\d{1,2})[.:](\d{2})\))?")


def _english_deadline_to_iso(text):
    # type: (str) -> str
    """«14-Oct-2026 18:00 (GMT 2.00)» → «2026-10-14T18:00+02:00» (UNGM),
    «20 October 2026» → «2026-10-20» (IsDB).

    Разборщик сроков знает только числовые даты, а точное время берёт лишь из
    ISO: без перевода срок был бы «нет срока», и закрытый лот считался бы
    живым. Не тот формат — строка возвращается как была.
    """
    m = _EN_DEADLINE_RE.search(text)
    token = m.group(2).lower() if m else ""
    if not m or not (token in _MONTHS_EN or token in _MONTH_NAMES_EN):
        return text
    day, mon, year = int(m.group(1)), _MONTHS_EN[token[:3]], m.group(3)
    out = "%s-%02d-%02d" % (year, mon, day)
    if m.group(4):
        out += "T%02d:%s" % (int(m.group(4)), m.group(5))
        if m.group(7):
            out += "%s%02d:%s" % (m.group(6) or "+", int(m.group(7)), m.group(8))
    return out


class HtmlAdapter(BaseAdapter):
    """Adapter for HTML scraping sources (httpx + BeautifulSoup)."""

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        if config.html_selectors is None:
            raise ValueError(
                "html_selectors required for HTML adapter (source: %s)" % config.id
            )
        self._fetch_error = None  # type: Optional[str]
        self._no_title = 0

    async def _fetch_items(self) -> List[RawTender]:
        """Fetch and parse HTML pages."""
        cfg = self.config
        selectors = cfg.html_selectors
        if selectors is None:
            return []

        all_items = []  # type: List[RawTender]

        # Use residential proxy for geo-restricted sources
        proxy_url = None  # type: Optional[str]
        if cfg.use_proxy and settings.residential_proxy_url:
            proxy_url = settings.residential_proxy_url
            logger.info("[%s] Using residential proxy", cfg.name)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout, connect=10.0),
            headers=cfg.headers,
            follow_redirects=True,
            proxy=proxy_url,
            verify=_ca_bundle(cfg),
        ) as client:
            if cfg.antiforgery_page:
                token = await self._antiforgery_token(client)
                if not token:
                    self.last_error = self._fetch_error
                    return []
                client.headers["RequestVerificationToken"] = token
            html = await self._fetch_page(client, cfg.url)
            if not html:
                # Раньше это был тихий ноль: «0 строк, ошибок нет» читалось как
                # «площадка ничего не публикует» (10.10: Tashkent Steel с
                # истёкшим сертификатом, Ипотека-банк и UNGM — каждый прогон).
                self.last_error = self._fetch_error or "страница не загрузилась"
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

            if selectors.detail_deadline_regex and all_items:
                await self._fill_deadlines_from_detail(client, all_items)

        return all_items

    async def _fill_deadlines_from_detail(self, client, items):
        # type: (httpx.AsyncClient, List[RawTender]) -> None
        """Срок подачи со страницы лота — для карточек, где его нет (см. HtmlSelectors).

        Без срока лот считается живым вечно, и закрытые конкурсы шли бы в
        алерты; со сроком их отсекает префильтр, а живые получают точную дату.
        Не нашли — срок остаётся пустым, как и было.
        """
        sel = self.config.html_selectors
        rx = re.compile(sel.detail_deadline_regex, re.I)
        budget = max(0, sel.detail_max)
        for t in items:
            if t.deadline or not t.source_url:
                continue
            if budget <= 0:
                break
            budget -= 1
            page = await self._fetch_page(client, t.source_url)
            if not page:
                continue
            text = re.sub(r"\s+", " ", BeautifulSoup(page, "html.parser").get_text(" "))
            m = rx.search(text)
            if not m:
                continue
            found = m.group(1).strip()
            # «5.10.2026» → «05.10.2026»: разборщик сроков ждёт две цифры.
            dm = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", found)
            if dm:
                found = "%02d.%02d.%s" % (int(dm.group(1)), int(dm.group(2)), dm.group(3))
            t.deadline = found
            t.date_end = found

    async def _antiforgery_token(self, client):
        # type: (httpx.AsyncClient) -> Optional[str]
        """Токен ASP.NET со страницы `antiforgery_page` (см. SourceConfig)."""
        page = self.config.antiforgery_page
        self._fetch_error = None
        try:
            await self.rate_limit()
            resp = await client.get(page)
            resp.raise_for_status()
        except Exception as exc:
            self._fetch_error = ("антифорджери-страница: %s: %s" % (
                type(exc).__name__, str(exc)))[:200]
            return None
        el = BeautifulSoup(resp.text, "html.parser").select_one(
            'input[name="__RequestVerificationToken"]')
        value = el.get("value") if el is not None else None
        if not value:
            self._fetch_error = "на %s нет __RequestVerificationToken" % page
            return None
        return str(value)

    async def _fetch_page(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[str]:
        """Fetch a single HTML page. Returns HTML string or None."""
        cfg = self.config

        # 2 попытки: корп-сайты (agmk.uz) интермиттентно рвут соединение
        # (RemoteProtocolError с пустым str()) — одиночный фейл ронял весь прогон.
        last_exc = None  # type: Optional[Exception]
        self._fetch_error = None  # type: Optional[str]
        for attempt in range(2):
            await self.rate_limit()
            try:
                if cfg.method.upper() == "POST":
                    resp = await client.post(url, json=cfg.body)
                else:
                    resp = await client.get(url, params=cfg.params)

                resp.raise_for_status()
                text = resp.text
                if not text or len(text) < 50:
                    self._fetch_error = "пустой ответ %s (%d байт)" % (url, len(text or ""))
                    return None
                return text
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(2)
        logger.warning("[%s] Failed to fetch %s: %s: %s", cfg.name, url,
                       type(last_exc).__name__, str(last_exc))
        self._fetch_error = ("%s: %s" % (type(last_exc).__name__, str(last_exc)))[:200]
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
        self._no_title = 0
        for idx, container in enumerate(containers):
            try:
                tender = self._parse_container(container, page_url, idx)
                if tender is not None:
                    results.append(tender)
            except Exception as exc:
                logger.debug(
                    "[%s] Skipping container %d: %s", cfg.name, idx, str(exc)
                )

        if not results and self._no_title == len(containers):
            # Карточки на странице есть, а заголовка нет ни у одной — значит,
            # селекторы полей отстали от вёрстки. Так с июня молчал Tashkent
            # Steel: 9 карточек находились, заголовок искался по старому классу
            # Elementor, и каждая отбрасывалась без звука (10.10.2026). Отсев
            # фильтром страны — другое: заголовки там есть, это не ошибка.
            self.last_error = (
                "селекторы устарели: %d карточек по «%s», ни одна не разобрана "
                "(заголовок «%s»)" % (len(containers), selectors.container, selectors.title))
            logger.warning("[%s] %s", cfg.name, self.last_error)

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
            self._no_title += 1
            return None

        # Extract optional fields
        organization = self._extract_field(container, selectors.organization) if selectors.organization else ""
        if not organization:
            organization = cfg.name

        deadline = self._extract_field(container, selectors.deadline) if selectors.deadline else None
        if deadline == "":
            deadline = None
        if deadline:
            deadline = _english_deadline_to_iso(deadline)

        # Дата публикации не должна попадать в срок подачи — см. HtmlSelectors.
        published = None  # type: Optional[str]
        if deadline is not None and selectors.deadline_is_publication_date:
            published, deadline = deadline, None

        price_str = self._extract_field(container, selectors.price) if selectors.price else None
        price = None  # type: Optional[float]
        if price_str:
            # Try to extract number from price string
            # Handle European format: "1.000.000,50" -> "1000000.50"
            cleaned = re.sub(r"[^\d.,]", "", price_str)
            if "," in cleaned and "." in cleaned:
                # Determine format by position: last separator is decimal
                last_comma = cleaned.rfind(",")
                last_dot = cleaned.rfind(".")
                if last_comma > last_dot:
                    # European: 1.000.000,50
                    cleaned = cleaned.replace(".", "").replace(",", ".")
                else:
                    # US: 1,000,000.50
                    cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                # Could be decimal comma (1234,50) or thousand separator (1,000)
                parts = cleaned.split(",")
                if len(parts) == 2 and len(parts[1]) <= 2:
                    cleaned = cleaned.replace(",", ".")
                else:
                    cleaned = cleaned.replace(",", "")
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
            tmpl = cfg.field_map.source_url_template
            if link.startswith("http"):
                # Абсолютный href: подстановка в шаблон "host{link}" дала бы
                # "https://hosthttps://..." — используем href напрямую.
                source_url = link
            elif tmpl and tmpl != "{link}":
                # Относительный href без ведущего "/" ломает шаблон "host{link}":
                # hamkorbank отдаёт 'press-center/tenders/<slug>/' (резолвится
                # только через <base href>) → 'hamkorbank.uzpress-center...'.
                norm = link if link.startswith("/") else "/" + link
                source_url = tmpl.replace(
                    "{link}", norm
                ).replace(
                    "{id}", norm
                ).replace(
                    "{external_id}", norm
                )
            else:
                source_url = urljoin(page_url, link)
        elif cfg.field_map.source_url_template and "{" not in cfg.field_map.source_url_template:
            # Link-less источники со статическим шаблоном (uzairports, gov-eco:
            # карточки без detail-страниц — модалки/таблицы). Раньше шаблон
            # игнорировался (ветка только под `if link:`) → source_url="" у
            # всех строк = алерт без ссылки вообще (П8 11.06).
            source_url = cfg.field_map.source_url_template

        # External ID: from link or generate stable hash from content
        ext_id = ""
        if link:
            id_regex = (selectors.external_id_regex or "") if selectors else ""
            if id_regex:
                m = re.search(id_regex, link)
                ext_id = m.group(1) if m else ""
            if not ext_id:
                # Try to extract ID from link
                id_match = re.search(r"(\d+)", link)
                if id_match:
                    ext_id = id_match.group(1)
                else:
                    ext_id = link.strip("/").split("/")[-1] if "/" in link else link
        if not ext_id:
            # Generate stable ID from content to avoid silent data loss on re-crawl
            # (using index would give different tenders the same ID across crawls)
            hash_input = "%s|%s|%s" % (title, organization, cfg.name)
            ext_id = hashlib.md5(hash_input.encode("utf-8")).hexdigest()[:12]

        tender_id = "%s-%s" % (cfg.id_prefix, ext_id)

        # Country filter (e.g. UNDP filtering for UZB)
        if cfg.country_filter:
            org_text = organization.upper()
            title_text = title.upper()
            # Check if country code appears in organization or full container text
            container_text = container.get_text().upper()
            if cfg.country_filter.upper() not in container_text:
                return None

        description = self._extract_field(container, selectors.description) if selectors.description else ""
        categories = []  # type: List[str]
        if selectors.categories:
            for el in container.select(selectors.categories):
                cat = el.get_text(" ", strip=True)
                if cat and cat not in categories:
                    categories.append(cat)

        # Search text
        search_parts = [title]
        if organization:
            search_parts.append(organization)
        if description:
            search_parts.append(description)
        search_text = " ".join(search_parts)[:2000]

        return RawTender(
            id=tender_id,
            external_id=ext_id,
            title=title,
            organization=organization,
            price=price,
            currency=cfg.field_map.currency if cfg.field_map.currency else "USD",
            deadline=deadline,
            date_start=published,
            date_end=deadline,
            region="",
            categories=categories,
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

        # Handle :nth-match(N) — select Nth element (0-indexed)
        nth_match = None  # type: Optional[int]
        nth_re = re.match(r"^(.+):nth-match\((\d+)\)$", selector)
        if nth_re:
            selector = nth_re.group(1)
            nth_match = int(nth_re.group(2))

        # Select element
        if nth_match is not None:
            els = container.select(selector)
            el = els[nth_match] if nth_match < len(els) else None
        else:
            el = container.select_one(selector)

        if el is None:
            return ""

        if attr_name:
            val = el.get(attr_name)
            return _safe_str(val)

        # Неразрывный пробел (&nbsp;) — обычный пробел: сайты на CMS ставят его
        # после предлогов («на&nbsp;услуги», Хамкорбанк 10.10), и фраза словаря
        # с обычным пробелом мимо такого заголовка промахивалась.
        return el.get_text(strip=True).replace("\xa0", " ")
