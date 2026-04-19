"""JSON-RPC 2.0 adapter for hayotbirja.uz and xt-xarid.uz."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from crawler.adapters.api import _apply_item_filter
from crawler.adapters.base import BaseAdapter
from crawler.core.models import RawTender, SourceConfig

logger = logging.getLogger(__name__)


def _safe_str(value):
    # type: (Any) -> str
    if value is None:
        return ""
    return str(value).strip()


def _safe_float(value):
    # type: (Any) -> Optional[float]
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _get_field(item, path, default=None):
    # type: (Dict[str, Any], str, Any) -> Any
    """Get field supporting dot-notation: 'company.title', 'meta.good_maps'."""
    if not path:
        return default
    parts = path.split(".")
    current = item
    for part in parts:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return default
        else:
            return default
    return current if current is not None else default


class JsonRpcAdapter(BaseAdapter):
    """Adapter for JSON-RPC 2.0 APIs (hayotbirja.uz, xt-xarid.uz).

    These platforms use POST requests with JSON-RPC body:
    {"jsonrpc":"2.0","method":"ref","id":1,"params":{"ref":"ref_name","op":"read","limit":N,"offset":M}}
    """

    def __init__(self, config):
        # type: (SourceConfig) -> None
        super().__init__(config)
        self._rpc_id = 0

    def _next_rpc_id(self):
        # type: () -> int
        self._rpc_id += 1
        return self._rpc_id

    async def _fetch_items(self):
        # type: () -> List[RawTender]
        cfg = self.config
        if not cfg.rpc_ref:
            logger.warning("[%s] No rpc_ref configured, skipping", cfg.name)
            return []

        all_items = []  # type: List[Dict[str, Any]]
        page_size = 100
        max_pages = 10
        if cfg.pagination:
            page_size = cfg.pagination.page_size
            max_pages = cfg.pagination.max_pages

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout, connect=10.0),
            headers=cfg.headers,
        ) as client:
            offset = 0
            for page_num in range(max_pages):
                await self.rate_limit()

                body = {
                    "jsonrpc": "2.0",
                    "method": cfg.rpc_method,
                    "id": self._next_rpc_id(),
                    "params": {
                        "ref": cfg.rpc_ref,
                        "op": "read",
                        "limit": page_size,
                        "offset": offset,
                    },
                }

                data = await self._make_request(client, body)
                if data is None:
                    break

                # JSON-RPC response: {"result": [...], "jsonrpc": "2.0", "id": N}
                # or {"error": {...}}
                if isinstance(data, dict) and "error" in data:
                    err = data["error"]
                    logger.warning(
                        "[%s] JSON-RPC error: %s",
                        cfg.name,
                        _safe_str(err.get("message", err))[:200],
                    )
                    break

                items = data.get("result", []) if isinstance(data, dict) else []
                if not isinstance(items, list):
                    items = []

                if not items:
                    break

                all_items.extend(items)

                if len(items) < page_size:
                    break

                offset += page_size

        # Client-side filter (status whitelist, etc.) before conversion
        if cfg.item_filter:
            before = len(all_items)
            all_items = _apply_item_filter(all_items, cfg.item_filter)
            logger.info(
                "[%s] item_filter %s: %d -> %d items",
                cfg.name, cfg.item_filter, before, len(all_items),
            )

        tenders = self._convert_all(all_items)
        return tenders

    async def _make_request(self, client, body):
        # type: (httpx.AsyncClient, Dict[str, Any]) -> Optional[Dict[str, Any]]
        """POST JSON-RPC request with retry/backoff."""
        cfg = self.config
        max_retries = 3

        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    cfg.url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                )

                if resp.status_code in (429, 503) and attempt < max_retries - 1:
                    wait = min(2 ** (attempt + 1), 30)
                    logger.warning(
                        "[%s] HTTP %d, retrying in %ds",
                        cfg.name, resp.status_code, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "[%s] %s, retrying in %ds",
                        cfg.name, type(exc).__name__, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

        return None

    def _convert_all(self, items):
        # type: (List[Dict[str, Any]]) -> List[RawTender]
        results = []  # type: List[RawTender]
        for item in items:
            try:
                tender = self._convert_item(item)
                if tender is not None:
                    results.append(tender)
            except Exception as exc:
                logger.debug("[%s] Skipping item: %s", self.config.name, str(exc))
        return results

    def _convert_item(self, item):
        # type: (Dict[str, Any]) -> Optional[RawTender]
        cfg = self.config
        fm = cfg.field_map

        # Build title from goods list for reduction items
        title = ""
        if fm.title == "_goods_title":
            title = self._build_goods_title(item)
        else:
            title = _safe_str(_get_field(item, fm.title, ""))

        if not title or len(title) < 3:
            return None

        organization = _safe_str(_get_field(item, fm.organization, "")) if fm.organization else ""
        price = _safe_float(_get_field(item, fm.price)) if fm.price else None
        currency = _safe_str(_get_field(item, fm.currency, "")) if fm.currency else "UZS"
        if not currency:
            currency = "UZS"

        deadline = _safe_str(_get_field(item, fm.deadline, "")) if fm.deadline else None
        if deadline == "":
            deadline = None

        region = _safe_str(_get_field(item, fm.region, "")) if fm.region else ""

        # External ID
        ext_id_val = ""
        if fm.external_id:
            ext_id_val = _safe_str(_get_field(item, fm.external_id, ""))
        if not ext_id_val:
            ext_id_val = _safe_str(item.get("id", ""))

        tender_id = "%s-%s" % (cfg.id_prefix, ext_id_val)

        # Source URL
        source_url = ""
        if fm.source_url_template:
            source_url = fm.source_url_template.replace(
                "{id}", _safe_str(item.get("id", ext_id_val))
            )

        # Search text — collect all goods names and categories
        search_text = self._build_search_text(item, cfg.keywords_fields)

        # Status
        status_raw = _safe_str(item.get("status", "")).lower()
        if status_raw in ("open", "publicated", "active"):
            status = "active"
        elif status_raw in ("close", "closed", "cancel", "cancelled", "not_realized"):
            status = "closed"
        else:
            status = "active"

        # Determine message_type / tender_type
        message_type = "tender"
        if cfg.rpc_ref and "reduction" in cfg.rpc_ref:
            message_type = "customer_request"  # reverse auction = customer request

        # Bid count from part_count
        bid_count = item.get("part_count", 0)

        return RawTender(
            id=tender_id,
            external_id=ext_id_val,
            title=title,
            organization=organization,
            price=price,
            currency=currency,
            deadline=deadline,
            region=region,
            source=cfg.name,
            source_url=source_url,
            status=status,
            search_text=search_text,
            message_type=message_type,
        )

    def _build_goods_title(self, item):
        # type: (Dict[str, Any]) -> str
        """Build title from meta.good_maps list — used for reduction items."""
        goods = _get_field(item, "meta.good_maps", [])
        if not goods or not isinstance(goods, list):
            # Fallback: try name field
            return _safe_str(item.get("name", ""))

        names = []
        for g in goods:
            if g and isinstance(g, dict):
                name = _safe_str(g.get("name", ""))
                if name and name not in names:
                    names.append(name)
        if not names:
            return ""
        # Join up to 5 product names
        return ", ".join(names[:5])

    def _build_search_text(self, item, keywords_fields):
        # type: (Dict[str, Any], List[str]) -> str
        """Build search text from goods names, categories, and other fields."""
        parts = []  # type: List[str]

        # Always include goods names and categories for searchability
        goods = _get_field(item, "meta.good_maps", [])
        if goods and isinstance(goods, list):
            for g in goods:
                if g and isinstance(g, dict):
                    name = _safe_str(g.get("name", ""))
                    if name:
                        parts.append(name)
                    cat = g.get("category")
                    if cat and isinstance(cat, dict):
                        cat_title = _safe_str(cat.get("title", ""))
                        if cat_title:
                            parts.append(cat_title)

        # Also include explicitly configured fields
        for kf in keywords_fields:
            if kf == "meta.good_maps":
                continue  # already handled above
            val = _safe_str(_get_field(item, kf, ""))
            if val:
                parts.append(val)

        return " ".join(parts)[:2000]
