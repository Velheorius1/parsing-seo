"""Bounded public specification enrichment for confirmed competitor awards."""
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple


ETENDER_DETAIL = "https://apietender.uzex.uz/api/common/GetTrade/%s/0"
DIRECT_DETAIL = "https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchase/%s"
MAX_SPECIFICATION_LENGTH = 280
MAX_LINE_ITEMS = 3


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _quantity(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else ("%s" % number).rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return _text(value)


def _first(item: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    for key in keys:
        value = _text(item.get(key))
        if value:
            return value
    return ""


def _summarize_items(items: Any) -> Optional[str]:
    if not isinstance(items, list):
        return None
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _first(item, ("Product_Name", "product_name", "ProductName", "name", "Name",
                             "Category_Name", "category_name"))
        description = _first(item, ("Description", "description", "Product_Description", "product_description"))
        quantity = _quantity(item.get("Quantity", item.get("quantity")))
        if not name and not description:
            continue
        line = name or description
        if name and quantity:
            line += " · " + quantity
        if name and description:
            line += "; " + description
        lines.append(line)
        if len(lines) == MAX_LINE_ITEMS:
            break
    if not lines:
        return None
    return _text(" | ".join(lines))[:MAX_SPECIFICATION_LENGTH].rstrip()


def summarize_etender_detail(payload: Dict[str, Any]) -> Optional[str]:
    """Parse ETender's JSON-string ``budget_products`` safely."""
    raw = payload.get("budget_products") if isinstance(payload, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    return _summarize_items(raw)


def summarize_direct_detail(payload: Dict[str, Any]) -> Optional[str]:
    """Parse the public Direct card's ``js_details`` safely."""
    return _summarize_items(payload.get("js_details") if isinstance(payload, dict) else None)


def _is_candidate(row: Dict[str, Any]) -> bool:
    return (row.get("is_win") is True and row.get("above_threshold") is True and
            row.get("currency") == "UZS" and bool(row.get("procedure_id")))


def enrich_awards(source_id: str, awards: List[Dict[str, Any]], get: Callable[..., Any],
                  max_details: int = 25) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Attach optional specification text to a bounded set of confirmed awards.

    Any individual public-card failure remains visible in the summary but never
    removes the independently confirmed award from the returned list.
    """
    if max_details < 1:
        raise ValueError("max_details must be positive")
    endpoint = {"etender_deals": ETENDER_DETAIL, "uzex_direct": DIRECT_DETAIL}.get(source_id)
    copied = [dict(row) for row in awards]
    summary = {"attempted": 0, "enriched": 0, "failed": 0, "capped": 0}
    if endpoint is None:
        return copied, summary
    candidates = [row for row in copied if _is_candidate(row)]
    summary["capped"] = max(0, len(candidates) - max_details)
    for row in candidates[max_details:]:
        row["_specification_status"] = "capped"
    parser = summarize_etender_detail if source_id == "etender_deals" else summarize_direct_detail
    for row in candidates[:max_details]:
        summary["attempted"] += 1
        try:
            response = get(endpoint % row["procedure_id"], timeout=20)
            response.raise_for_status()
            specification = parser(response.json())
            if not specification:
                raise ValueError("detail has no readable line items")
            row["specification_text"] = specification
            row["_specification_status"] = "complete"
            summary["enriched"] += 1
        except Exception:
            row["_specification_status"] = "unavailable"
            summary["failed"] += 1
    return copied, summary
