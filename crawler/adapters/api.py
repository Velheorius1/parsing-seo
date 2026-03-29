"""API adapter — fetches tenders from JSON REST APIs."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from crawler.adapters.base import BaseAdapter
from crawler.core.models import RawTender, SourceConfig

logger = logging.getLogger(__name__)


def _resolve_path(data: Any, path: str) -> Any:
    """Navigate nested dict/list via dot-path like 'data.items' or '0.total_count'."""
    for key in path.split("."):
        if data is None:
            return None
        if isinstance(data, list):
            try:
                data = data[int(key)]
            except (IndexError, ValueError):
                return None
        elif isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def _safe_float(value: Any) -> Optional[float]:
    """Convert value to float, return None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _safe_str(value: Any) -> str:
    """Convert value to string, return empty string for None."""
    if value is None:
        return ""
    return str(value).strip()


def _get_field(item: Dict[str, Any], path: str, default: Any = None) -> Any:
    """Get field from item, supporting dot-notation for nested objects."""
    if "." in path:
        return _resolve_path(item, path)
    return item.get(path, default)


class ApiAdapter(BaseAdapter):
    """Adapter for JSON API sources (GET/POST with httpx)."""

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)

    def _inject_auth(self):
        # type: () -> bool
        """Load token from SessionStore and inject into headers. Returns False if no valid token."""
        cfg = self.config
        if not cfg.auth_platform:
            return True
        try:
            from crawler.auth.session_store import session_store
            token = session_store.get_token(cfg.auth_platform)
            if not token:
                return False
            prefix = cfg.auth_header_prefix
            if prefix:
                cfg.headers[cfg.auth_header_name] = "%s %s" % (prefix, token)
            else:
                cfg.headers[cfg.auth_header_name] = token
            return True
        except Exception as exc:
            logger.warning("[%s] Auth error: %s", cfg.name, str(exc)[:80])
            return False

    async def _fetch_items(self) -> List[RawTender]:
        """Fetch all items from the API, handling pagination."""
        cfg = self.config

        # Inject auth token if configured
        if cfg.auth_platform:
            if not self._inject_auth():
                logger.warning(
                    "[%s] No valid token for '%s', skipping",
                    cfg.name, cfg.auth_platform,
                )
                return []

        all_items = []  # type: List[Dict[str, Any]]

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout, connect=10.0),
            headers=cfg.headers,
        ) as client:
            if cfg.pagination is not None:
                all_items = await self._fetch_paginated(client)
            else:
                raw = await self._make_request(
                    client, cfg.url, cfg.method, cfg.params, cfg.body
                )
                all_items = self._extract_items(raw)

        tenders = self._convert_all(all_items)

        # Apply country_filter if set (e.g. World Bank returns all countries)
        if cfg.country_filter:
            cf = cfg.country_filter.lower()
            tenders = [
                t for t in tenders
                if cf in t.region.lower()
                or cf in t.source.lower()
                or cf in t.search_text.lower()
            ]
            logger.info(
                "[%s] Country filter '%s': %d -> %d tenders",
                cfg.name, cfg.country_filter, len(all_items), len(tenders),
            )

        return tenders

    async def _fetch_paginated(
        self, client: httpx.AsyncClient
    ) -> List[Dict[str, Any]]:
        """Handle pagination: offset, page, or cursor."""
        cfg = self.config
        pag = cfg.pagination
        if pag is None:
            return []

        all_items = []  # type: List[Dict[str, Any]]
        pag_type = pag.type

        if pag_type == "offset":
            all_items = await self._paginate_offset(client, pag)
        elif pag_type == "page":
            all_items = await self._paginate_page(client, pag)
        elif pag_type == "cursor":
            all_items = await self._paginate_cursor(client, pag)
        else:
            logger.warning(
                "[%s] Unknown pagination type: %s, fetching single page",
                cfg.name,
                pag_type,
            )
            raw = await self._make_request(
                client, cfg.url, cfg.method, cfg.params, cfg.body
            )
            all_items = self._extract_items(raw)

        return all_items

    async def _paginate_offset(
        self, client: httpx.AsyncClient, pag: Any
    ) -> List[Dict[str, Any]]:
        """Offset-based pagination (from/to pattern like UZEX APIs)."""
        cfg = self.config
        all_items = []  # type: List[Dict[str, Any]]
        offset = 0
        page_size = pag.page_size

        for page_num in range(pag.max_pages):
            await self.rate_limit()

            # Build body/params with pagination
            body = dict(cfg.body) if cfg.body else {}
            params = dict(cfg.params) if cfg.params else None

            if cfg.method.upper() == "POST":
                body[pag.param] = offset
                if pag.size_param:
                    body[pag.size_param] = offset + page_size
            else:
                if params is None:
                    params = {}
                params[pag.param] = offset
                if pag.size_param:
                    params[pag.size_param] = offset + page_size

            raw = await self._make_request(
                client, cfg.url, cfg.method, params, body if cfg.body else None
            )
            items = self._extract_items(raw)

            if not items:
                break

            all_items.extend(items)

            # Check total_field to know when to stop
            if pag.total_field:
                total = _resolve_path(raw, pag.total_field)
                if total is not None:
                    try:
                        total_count = int(total)
                        if offset + page_size >= total_count:
                            break
                    except (ValueError, TypeError):
                        pass

            # If fewer items than page_size, we've reached the end
            if len(items) < page_size:
                break

            offset += page_size

        return all_items

    async def _paginate_page(
        self, client: httpx.AsyncClient, pag: Any
    ) -> List[Dict[str, Any]]:
        """Page-number based pagination."""
        cfg = self.config
        all_items = []  # type: List[Dict[str, Any]]

        start = getattr(pag, 'page_start', 0)
        for page_num in range(start, start + pag.max_pages):
            await self.rate_limit()

            body = dict(cfg.body) if cfg.body else {}
            params = dict(cfg.params) if cfg.params else None

            if cfg.method.upper() == "POST":
                body[pag.param] = page_num
                if pag.size_param:
                    body[pag.size_param] = pag.page_size
            else:
                if params is None:
                    params = {}
                params[pag.param] = page_num
                if pag.size_param:
                    params[pag.size_param] = pag.page_size

            raw = await self._make_request(
                client, cfg.url, cfg.method, params, body if cfg.body else None
            )
            items = self._extract_items(raw)

            if not items:
                break

            all_items.extend(items)

            if len(items) < pag.page_size:
                break

        return all_items

    async def _paginate_cursor(
        self, client: httpx.AsyncClient, pag: Any
    ) -> List[Dict[str, Any]]:
        """Cursor-based pagination."""
        cfg = self.config
        all_items = []  # type: List[Dict[str, Any]]
        cursor = None  # type: Optional[str]

        for page_num in range(pag.max_pages):
            await self.rate_limit()

            body = dict(cfg.body) if cfg.body else {}
            params = dict(cfg.params) if cfg.params else None

            if cursor is not None:
                if cfg.method.upper() == "POST":
                    body[pag.param] = cursor
                else:
                    if params is None:
                        params = {}
                    params[pag.param] = cursor

            raw = await self._make_request(
                client, cfg.url, cfg.method, params, body if cfg.body else None
            )
            items = self._extract_items(raw)

            if not items:
                break

            all_items.extend(items)

            # Look for next cursor in response
            if pag.size_param and isinstance(raw, dict):
                next_cursor = raw.get(pag.size_param)
                if next_cursor:
                    cursor = str(next_cursor)
                else:
                    break
            else:
                break

        return all_items

    async def _make_request(
        self,
        client: httpx.AsyncClient,
        url: str,
        method: str,
        params: Optional[Dict[str, Any]],
        body: Optional[Dict[str, Any]],
    ) -> Any:
        """Make a single HTTP request with retry/backoff. Returns parsed JSON."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if method.upper() == "POST":
                    resp = await client.post(url, json=body, params=params)
                else:
                    resp = await client.get(url, params=params)

                # Auth expired — mark token and skip
                if resp.status_code in (401, 403) and self.config.auth_platform:
                    logger.warning(
                        "[%s] HTTP %d — token expired for '%s'",
                        self.config.name, resp.status_code, self.config.auth_platform,
                    )
                    try:
                        from crawler.auth.session_store import session_store
                        session_store.mark_expired(self.config.auth_platform)
                    except Exception:
                        pass
                    raise httpx.HTTPStatusError(
                        "Auth expired", request=resp.request, response=resp
                    )

                if resp.status_code in (429, 503) and attempt < max_retries - 1:
                    retry_after = resp.headers.get("Retry-After")
                    wait = min(int(retry_after), 60) if retry_after and retry_after.isdigit() else min(2 ** (attempt + 1), 30)
                    logger.warning(
                        "[%s] HTTP %d, retrying in %ds (attempt %d/%d)",
                        self.config.name, resp.status_code, wait, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()

                try:
                    return resp.json()
                except Exception as json_exc:
                    logger.warning("[%s] Invalid JSON from %s: %s", self.config.name, url, str(json_exc)[:80])
                    raise

            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "[%s] %s, retrying in %ds (attempt %d/%d)",
                        self.config.name, type(exc).__name__, wait, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

    def _extract_items(self, raw: Any) -> List[Dict[str, Any]]:
        """Extract list of items from response, navigating via response_path."""
        cfg = self.config

        # Navigate to nested data if response_path is set
        if cfg.response_path:
            data = _resolve_path(raw, cfg.response_path)
        else:
            data = raw

        if data is None:
            return []

        # World Bank returns dict of dicts: {id: {fields...}}
        if isinstance(data, dict):
            return list(data.values())

        if isinstance(data, list):
            return data

        return []

    def _convert_all(self, items: List[Dict[str, Any]]) -> List[RawTender]:
        """Convert raw JSON items to RawTender using field_map."""
        results = []  # type: List[RawTender]
        for item in items:
            try:
                tender = self._convert_item(item)
                if tender is not None:
                    results.append(tender)
            except Exception as exc:
                logger.debug(
                    "[%s] Skipping item: %s", self.config.name, str(exc)
                )
        return results

    def _convert_item(self, item: Dict[str, Any]) -> Optional[RawTender]:
        """Convert a single JSON object to RawTender using field_map."""
        cfg = self.config
        fm = cfg.field_map

        # Extract fields via field_map (supports dot-notation: "customer.name")
        title = _safe_str(_get_field(item, fm.title, ""))
        if not title or len(title) < 3:
            return None

        organization = _safe_str(_get_field(item, fm.organization, "")) if fm.organization else ""
        price = _safe_float(_get_field(item, fm.price)) if fm.price else None
        currency = _safe_str(_get_field(item, fm.currency, "")) if fm.currency else ""
        if not currency:
            currency = "UZS"

        deadline = _safe_str(_get_field(item, fm.deadline, "")) if fm.deadline else None
        if deadline == "":
            deadline = None

        date_start = _safe_str(_get_field(item, fm.date_start, "")) if fm.date_start else None
        if date_start == "":
            date_start = None

        date_end = _safe_str(_get_field(item, fm.date_end, "")) if fm.date_end else None
        if date_end == "":
            date_end = None

        region = _safe_str(_get_field(item, fm.region, "")) if fm.region else ""

        # Categories
        categories = []  # type: List[str]
        if fm.categories:
            cat_val = _get_field(item, fm.categories, "")
            if isinstance(cat_val, list):
                categories = [str(c) for c in cat_val if c]
            elif cat_val:
                categories = [str(cat_val)]

        # External ID
        ext_id_val = ""
        if fm.external_id:
            ext_id_val = _safe_str(_get_field(item, fm.external_id, ""))
        if not ext_id_val:
            ext_id_val = _safe_str(_get_field(item, "id", ""))

        # Generate prefixed ID
        tender_id = "%s-%s" % (cfg.id_prefix, ext_id_val)

        # Source URL from template
        source_url = ""
        if fm.source_url_template:
            try:
                source_url = fm.source_url_template.replace(
                    "{id}", _safe_str(item.get("id", ext_id_val))
                ).replace(
                    "{link}", ext_id_val
                )
            except Exception:
                source_url = ""

        # Search text from keywords_fields (supports dot-notation)
        search_parts = []  # type: List[str]
        for kf in cfg.keywords_fields:
            val = _safe_str(_get_field(item, kf, ""))
            if val:
                search_parts.append(val)
        search_text = " ".join(search_parts)[:2000]

        # Status based on deadline
        status = "active"
        if deadline:
            try:
                dl = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                if dl < datetime.utcnow().replace(
                    tzinfo=dl.tzinfo if dl.tzinfo else None
                ):
                    status = "closed"
            except (ValueError, TypeError):
                pass

        return RawTender(
            id=tender_id,
            external_id=ext_id_val,
            title=title,
            organization=organization,
            price=price,
            currency=currency,
            deadline=deadline,
            date_start=date_start,
            date_end=date_end,
            region=region,
            categories=categories,
            source=cfg.name,
            source_url=source_url,
            status=status,
            search_text=search_text,
        )
