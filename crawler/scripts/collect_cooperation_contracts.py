#!/usr/bin/env python3
"""Read-only, receipt-backed Cooperation public-contract collector.

This is deliberately separate from the authenticated production crawler: it
uses only the public statistics endpoint, imports neither settings, Supabase,
nor Telegram, and records enough response metadata to audit the outcome.  The
endpoint's ``total`` is authoritative for a captured run; a page cap is never
reported as a complete archive.
"""
import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

from crawler.core.competitor_audit import entity_for_inn, load_registry, normalize_inn


ENDPOINT = "https://stat-new.cooperation.uz/gateway/api-stat/auction-contracts"
SOURCE = "Cooperation.uz Публичный реестр договоров"


def client_kwargs() -> Dict[str, Any]:
    """Use the existing dedicated Cooperation proxy when explicitly supplied."""
    proxy = os.getenv("COMPETITOR_COOP_PROXY_URL", "").strip()
    return {"proxy": proxy} if proxy else {}


def _date(value: Any) -> date:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("ru") or value.get("uz") or value.get("cyrl") or "")
    return str(value or "")


def _title(row: Dict[str, Any]) -> str:
    return "; ".join(_localized(product.get("name")) for product in row.get("products") or []
                     if isinstance(product, dict) and _localized(product.get("name")))


def normalize(row: Dict[str, Any], registry: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    winner_inn = normalize_inn(row.get("producerTin"))
    entity = entity_for_inn(registry, winner_inn)
    return {
        "procedure_id": str(row.get("id") or ""),
        "contract_number": str(row.get("contractNumber") or ""),
        "awarded_at": row.get("dealTime"),
        "winner_name": _localized(row.get("producerName")),
        "winner_inn": winner_inn,
        "competitor": entity.get("name") if entity else None,
        "buyer_name": _localized(row.get("customerName")),
        "title": _title(row),
        "lot_number": str(row.get("lotNumber") or ""),
        "amount": row.get("contractAmount"),
        # This endpoint has no currency field.  It is reported as an explicit
        # source assumption, not silently fed to UZS thresholding.
        "currency": None,
        "currency_note": "public Cooperation contract registry omits currency",
        "source": SOURCE,
        "source_url": ENDPOINT,
        "raw": row,
    }


def collect(date_from: date, page_size: int, page_cap: int,
            registry: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    pages = []
    normalized = []
    total = None
    completion = "page_cap"
    # The public endpoint rejects httpx's default user agent with 403, while
    # serving its own browser UI without authentication.  This is compatibility
    # with that public UI, not a credential or an access-control bypass.
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=30, headers=headers, **client_kwargs()) as client:
        for page_index in range(page_cap):
            params = {"skip": page_index * page_size, "take": page_size}
            response = client.get(ENDPOINT, params=params)
            response.raise_for_status()
            raw_bytes = response.content
            payload = response.json()
            envelope = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            rows = envelope.get("content") if isinstance(envelope, dict) else None
            if not isinstance(rows, list):
                raise ValueError("unexpected Cooperation response schema")
            if total is None:
                total = envelope.get("total")
                if not isinstance(total, int) or total < 0:
                    raise ValueError("missing or invalid Cooperation total")
            pages.append({"page": page_index + 1, "params": params,
                          "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows),
                          "reported_total": total})
            normalized.extend(normalize(row, registry) for row in rows if isinstance(row, dict))
            if page_index * page_size + len(rows) >= total:
                completion = "reported_total_reached"
                break
            if not rows:
                completion = "unexpected_empty_page"
                break
    in_window = []
    undated_rows = 0
    for row in normalized:
        try:
            if _date(row["awarded_at"]) >= date_from:
                in_window.append(row)
        except (TypeError, ValueError):
            undated_rows += 1
    complete = completion == "reported_total_reached"
    return {"source": SOURCE, "endpoint": ENDPOINT, "date_from": date_from.isoformat(),
            "page_size": page_size, "page_cap": page_cap, "reported_total": total,
            "pages_collected": len(pages), "completion": completion, "complete": complete,
            "undated_rows": undated_rows, "raw_pages": pages, "rows": in_window,
            "archive_scope_note": "Public endpoint depth is limited by its reported total; it is not evidence of a full annual archive."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--page-cap", type=int, default=20)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.page_size < 1 or args.page_cap < 1:
        parser.error("page size and cap must be positive")
    result = collect(date.fromisoformat(args.date_from), args.page_size, args.page_cap, load_registry())
    result.update({"captured_at": datetime.now(timezone.utc).isoformat(), "mode": "public_api_read_only"})
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output), "complete": result["complete"],
                      "reported_total": result["reported_total"], "rows": len(result["rows"]),
                      "completion": result["completion"]}, ensure_ascii=False))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
