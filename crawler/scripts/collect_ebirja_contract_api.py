#!/usr/bin/env python3
"""Read-only, receipt-backed public Ebirja contract archive collector.

Uses the API the public Ebirja contract UI calls.  It never imports settings,
Supabase or Telegram.  A source is complete only after its dated feed crosses
the requested lower boundary; a page cap is explicitly incomplete.
"""
import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx


BASE_URL = "https://xarid-api.ebirja.uz"
SOURCES = {
    "shop": {"path": "/common/contract/shop-list", "params": {"type": "e-shop"},
             "source": "Ebirja Договоры (Э-магазин)"},
    "auction": {"path": "/common/contract/external-auction", "params": {},
                "source": "Ebirja Договоры (Аукцион)"},
    "tender": {"path": "/common/contract/external-tender", "params": {"type": 1},
               "source": "Ebirja Договоры (Тендер)"},
    "selection": {"path": "/common/contract/external-tender", "params": {"type": 2},
                  "source": "Ebirja Договоры (Отбор)"},
}


def _date(value: Any) -> date:
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").date()


def normalize(source_key: str, row: Dict[str, Any]) -> Dict[str, Any]:
    tender = row.get("tender") or {}
    order = row.get("order") or {}
    return {
        "procedure_id": str(row.get("id") or ""),
        "contract_number": str(row.get("number") or ""),
        "awarded_at": row.get("created_at"),
        "winner_name": (row.get("producer") or {}).get("title") or "",
        "buyer_name": (row.get("customer") or {}).get("title") or "",
        "title": tender.get("title") or row.get("title") or "",
        "lot_number": tender.get("lot") or order.get("lot_number") or row.get("lot") or "",
        "amount": row.get("price"),
        "currency": row.get("currency") or "UZS",
        "source": SOURCES[source_key]["source"],
        "source_url": "%s%s" % (BASE_URL, SOURCES[source_key]["path"]),
        "raw": row,
    }


def collect_source(source_key: str, date_from: date, page_size: int, page_cap: int,
                   client_factory=httpx.Client) -> Dict[str, Any]:
    spec = SOURCES[source_key]
    pages = []
    normalized = []
    completion = "page_cap"
    with client_factory(timeout=30, headers={"Accept": "application/json"}) as client:
        for page in range(page_cap):
            params = dict(spec["params"])
            params.update({"currentPage": page, "perPage": page_size})
            response = client.get(BASE_URL + spec["path"], params=params)
            response.raise_for_status()
            raw_bytes = response.content
            payload = response.json()
            if not isinstance(payload, dict):
                completion = "invalid_payload_schema"
                break
            result = payload.get("result")
            if not isinstance(result, dict):
                completion = "invalid_result_schema"
                break
            rows = result.get("data")
            meta = result.get("meta")
            if not isinstance(rows, list):
                completion = "invalid_rows_schema"
                break
            if not isinstance(meta, dict):
                completion = "invalid_meta_schema"
                break
            pages.append({
                "page": page,
                "params": params,
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "rows": rows,
                "meta": meta,
            })
            if not rows:
                completion = "empty_page"
                break
            page_normalized = [normalize(source_key, row) for row in rows]
            normalized.extend(page_normalized)
            try:
                dates = [_date(row["awarded_at"]) for row in page_normalized]
            except (TypeError, ValueError):
                completion = "unreliable_dates"
                break
            if max(dates) < date_from:
                completion = "date_boundary"
                break
            page_count = meta.get("pageCount")
            if isinstance(page_count, bool):
                completion = "invalid_page_count"
                break
            try:
                page_count = int(page_count)
            except (TypeError, ValueError):
                completion = "invalid_page_count"
                break
            if page_count < 1:
                completion = "invalid_page_count"
                break
            if page + 1 >= page_count:
                completion = "page_count_end"
                break
    in_window = []
    for row in normalized:
        try:
            if _date(row["awarded_at"]) >= date_from:
                in_window.append(row)
        except (TypeError, ValueError):
            pass
    complete = completion in ("date_boundary", "empty_page", "page_count_end")
    return {
        "source_key": source_key, "source": spec["source"], "endpoint": BASE_URL + spec["path"],
        "page_size": page_size, "page_cap": page_cap, "pages_collected": len(pages),
        "completion": completion, "complete": complete, "raw_pages": pages, "rows": in_window,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", choices=list(SOURCES) + ["all"], default="all")
    parser.add_argument("--date-from", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--page-cap", type=int, default=150)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.page_size < 1 or args.page_cap < 1:
        parser.error("page size and cap must be positive")
    date_from = date.fromisoformat(args.date_from)
    keys = list(SOURCES) if args.type == "all" else [args.type]
    sources = [collect_source(key, date_from, args.page_size, args.page_cap) for key in keys]
    result = {"captured_at": datetime.now(timezone.utc).isoformat(), "mode": "public_api_read_only",
              "date_from": date_from.isoformat(), "complete": all(s["complete"] for s in sources),
              "sources": sources}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output), "complete": result["complete"],
                      "sources": [(s["source_key"], s["pages_collected"], len(s["rows"]), s["completion"])
                                  for s in sources]}, ensure_ascii=False))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
