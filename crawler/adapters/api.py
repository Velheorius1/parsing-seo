"""API adapter — fetches tenders from JSON REST APIs."""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from crawler.adapters.base import BaseAdapter
from crawler.config.settings import settings
from crawler.core.models import RawTender, SourceConfig

logger = logging.getLogger(__name__)


# Normalisation helpers for stable hash-based external_id (see field_map.external_id = "hash:...").
# Goal: minor textual edits (typos, trailing whitespace, year updates) produce the SAME id.
_DATE_RE = re.compile(
    r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b"     # 22.04.2026, 17-04-25
    r"|\b\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\b"      # 2026-04-22
    r"|\b\d{4}\s*(?:г|год|года|года\.|г\.)\b"     # 2026г, 2026 года
    r"|\b20\d{2}\b"                                 # bare 4-digit years
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _stable_hash_id(value):
    # type: (str) -> str
    """16-char SHA1 of normalized text. Empty string if value is empty."""
    if not value:
        return ""
    t = value.lower()
    t = _DATE_RE.sub(" ", t)
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    if not t:
        return ""
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


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


_TEMPLATE_RE = re.compile(r"\{([^{}]+)\}")


def _resolve_wildcard_path(item, path):
    # type: (Any, str) -> List[Any]
    """Resolve dot-path possibly containing [*] list wildcards. Returns list of values."""
    # Normalize [*] to a single * segment
    parts = path.replace("[*]", ".*").split(".")
    current = [item]  # type: List[Any]
    for part in parts:
        if part == "":
            continue
        next_level = []  # type: List[Any]
        for v in current:
            if part == "*":
                if isinstance(v, list):
                    next_level.extend(v)
            elif isinstance(v, dict):
                if part in v:
                    next_level.append(v[part])
            elif isinstance(v, list):
                try:
                    next_level.append(v[int(part)])
                except (IndexError, ValueError):
                    pass
        current = next_level
    return current


def _scalar_compare(a, op, b):
    # type: (Any, str, Any) -> bool
    """Compare a op b. Supports eq/ne/in/nin/gt/gte/lt/lte."""
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op == "in":
        try:
            return a in b  # type: ignore[operator]
        except TypeError:
            return False
    if op == "nin":
        try:
            return a not in b  # type: ignore[operator]
        except TypeError:
            return True
    try:
        a_num = float(a) if a is not None else None
        b_num = float(b) if b is not None else None
    except (TypeError, ValueError):
        return False
    if a_num is None or b_num is None:
        return False
    if op == "gt":
        return a_num > b_num
    if op == "gte":
        return a_num >= b_num
    if op == "lt":
        return a_num < b_num
    if op == "lte":
        return a_num <= b_num
    return False


def _match_predicate(item, path, pred):
    # type: (Any, str, Any) -> bool
    """
    Match item[path] against pred. pred is either:
      - scalar → equality
      - dict {op: value} → scalar compare
    Wildcard paths (containing [*]) use "any" semantics.
    """
    if isinstance(pred, dict) and len(pred) == 1:
        op, expected = next(iter(pred.items()))
    else:
        op, expected = "eq", pred

    wildcard = "[*]" in path or ".*" in path
    values = _resolve_wildcard_path(item, path)

    if wildcard:
        return any(_scalar_compare(v, op, expected) for v in values)
    if not values:
        # Missing field: "ne" and "nin" are trivially true; other ops false.
        return op in ("ne", "nin")
    return _scalar_compare(values[0], op, expected)


def _apply_item_filter(items, item_filter):
    # type: (List[Dict[str, Any]], Optional[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Keep only items where every entry in item_filter matches."""
    if not item_filter:
        return items
    result = []  # type: List[Dict[str, Any]]
    for it in items:
        if all(_match_predicate(it, p, pred) for p, pred in item_filter.items()):
            result.append(it)
    return result


def _resolve_extra_info_value(item, spec):
    # type: (Dict[str, Any], str) -> str
    """Resolve extra_info value. If spec contains {...} → template; else → dot-path."""
    if not spec:
        return ""
    if "{" in spec and "}" in spec:
        def repl(m):
            # type: (Any) -> str
            p = m.group(1).strip()
            vals = _resolve_wildcard_path(item, p)
            if not vals:
                return ""
            return _safe_str(vals[0])

        rendered = _TEMPLATE_RE.sub(repl, spec)
        stripped = rendered.strip()
        if not re.sub(r"[\s\-–—,.:;/]+", "", stripped):
            return ""
        return stripped
    vals = _resolve_wildcard_path(item, spec)
    if not vals:
        return ""
    return _safe_str(vals[0])


def _compute_sum_price(item, spec):
    # type: (Dict[str, Any], str) -> Optional[float]
    """Aggregate price from an array of line items. Spec: 'sum:<arr>:<qty>:<price>'."""
    parts = spec.split(":", 3)
    if len(parts) != 4 or parts[0] != "sum":
        return None
    _, arr_path, qty_field, price_field = parts
    arr = _get_field(item, arr_path, [])
    if not isinstance(arr, list):
        return None
    total = 0.0
    for row in arr:
        if not isinstance(row, dict):
            continue
        q = _safe_float(row.get(qty_field)) or 0
        p = _safe_float(row.get(price_field)) or 0
        total += q * p
    return total if total > 0 else None


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
                # RISK-1 / task #6 — distinguish auth-skip from empty-API.
                # Zero-result tracker ignores sources flagged as skipped_no_auth.
                self.last_skipped_no_auth = True
                logger.warning(
                    "[%s] No valid token for '%s', skipping",
                    cfg.name, cfg.auth_platform,
                )
                return []

        all_items = []  # type: List[Dict[str, Any]]

        # Use residential proxy for geo-restricted sources
        proxy_url = None  # type: Optional[str]
        if cfg.use_proxy and settings.residential_proxy_url:
            proxy_url = settings.residential_proxy_url
            logger.info("[%s] Using residential proxy", cfg.name)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout, connect=10.0),
            headers=cfg.headers,
            proxy=proxy_url,
        ) as client:
            if cfg.pagination is not None:
                all_items = await self._fetch_paginated(client)
            else:
                raw = await self._make_request(
                    client, cfg.url, cfg.method, cfg.params, cfg.body
                )
                all_items = self._extract_items(raw)

        # Client-side item filter (e.g. drop noise items with zero quantity/price)
        if cfg.item_filter:
            before = len(all_items)
            all_items = _apply_item_filter(all_items, cfg.item_filter)
            logger.info(
                "[%s] item_filter %s: %d -> %d items",
                cfg.name, cfg.item_filter, before, len(all_items),
            )

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
        if fm.price and fm.price.startswith("sum:"):
            price = _compute_sum_price(item, fm.price)
        elif fm.price:
            price = _safe_float(_get_field(item, fm.price))
        else:
            price = None
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
        # Supports two forms:
        #   1. "field" or "a.b.c"            → raw field value
        #   2. "hash:field" or "hash:a.b.c"  → stable 16-char SHA1 of normalized
        #      field value (lowercase, punctuation stripped, whitespace collapsed,
        #      date patterns removed). Use when source lacks a stable id/slug and
        #      the chosen text field may drift slightly between crawls.
        ext_id_val = ""
        if fm.external_id:
            spec = fm.external_id
            if spec.startswith("hash:"):
                raw = _safe_str(_get_field(item, spec[5:], ""))
                ext_id_val = _stable_hash_id(raw)
            else:
                ext_id_val = _safe_str(_get_field(item, spec, ""))
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
                now_utc = datetime.now(timezone.utc)
                if dl.tzinfo is None:
                    now_utc = now_utc.replace(tzinfo=None)
                if dl < now_utc:
                    status = "closed"
            except (ValueError, TypeError):
                pass

        # Extra info for TG alert (configurable per-source)
        extra_info = {}  # type: Dict[str, str]
        if fm.extra_info:
            for label, spec in fm.extra_info.items():
                value = _resolve_extra_info_value(item, spec)
                if value:
                    extra_info[label] = value

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
            extra_info=extra_info,
        )
