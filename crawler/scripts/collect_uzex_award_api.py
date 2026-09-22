#!/usr/bin/env python3
"""Bounded public UZEX award collector with exact-INN matching.

The endpoint is read-only. A run is complete only when every collected row has
a parseable award date and the oldest one crosses ``date_from``; a page cap is
never silently treated as an empty result.
"""
import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

from crawler.core.competitor_audit import normalize_award, registry_inns, load_registry, normalize_inn


SOURCES = {
    "deals": ("https://apietender.uzex.uz/api/common/DealsList", "deals"),
    "direct": ("https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases", "direct"),
}


def _award_date(row: Dict[str, Any]) -> date:
    value = str(row.get("deal_date") or row.get("contract_date") or "")[:10]
    return date.fromisoformat(value)


def collect(source: str, date_from: date, page_size: int, page_cap: int,
            post=httpx.post) -> Dict[str, Any]:
    endpoint, normalizer = SOURCES[source]
    target_inns = set(registry_inns(load_registry()))
    rows, receipts = [], []
    completion = "page_cap"
    for page in range(page_cap):
        start = page * page_size + 1
        body = ({"From": start, "To": start + page_size, "System_Id": 0}
                if source == "deals" else {"from": start, "to": start + page_size})
        response = post(endpoint, json=body, headers={"Content-Type": "application/json"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            completion = "invalid_payload_schema"
            receipts.append({"page": page + 1, "body": body, "rows": None,
                             "payload_type": type(payload).__name__})
            break
        batch = payload
        receipts.append({"page": page + 1, "body": body, "rows": len(batch)})
        if not batch:
            completion = "empty_page"
            break
        rows.extend(batch)
        try:
            dates = [_award_date(item) for item in batch]
        except (ValueError, TypeError):
            completion = "unreliable_dates"
            break
        if min(dates) < date_from:
            completion = "date_boundary"
            break
        if len(batch) < page_size:
            completion = "short_page"
            break
    awards = []
    for item in rows:
        normalized = normalize_award(normalizer, item)
        if normalize_inn(normalized.get("winner_inn")) not in target_inns:
            continue
        try:
            if _award_date(item) >= date_from:
                awards.append(normalized)
        except (ValueError, TypeError):
            pass
    complete = completion in ("empty_page", "date_boundary", "short_page")
    return {"source": source, "endpoint": endpoint, "date_from": date_from.isoformat(),
            "complete": complete, "completion": completion, "receipts": receipts, "awards": awards,
            "captured_at": datetime.now(timezone.utc).isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--page-cap", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = collect(args.source, date.fromisoformat(args.date_from), args.page_size, args.page_cap)
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"complete": result["complete"], "completion": result["completion"], "awards": len(result["awards"])}))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
