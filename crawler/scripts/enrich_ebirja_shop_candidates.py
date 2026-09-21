#!/usr/bin/env python3
"""Enrich bounded Ebirja shop name-candidates with their public contract card.

The archive list omits supplier INN and product details.  This tool first
selects only review candidates from a previously captured archive and then
fetches at most ``--max-details`` public cards.  It never searches the whole
archive live, and it never writes Supabase or Telegram.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

from crawler.core.competitor_audit import above_threshold, load_registry, name_candidates, normalize_inn


DETAIL_ENDPOINT = "https://xarid-api.ebirja.uz/common/contract/shop-view"


def candidate_rows(snapshot: Dict[str, Any], registry: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Select only high-value shop rows whose public winner name needs review."""
    selected = []
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else [snapshot]
    for source in sources:
        if source.get("source_key") != "shop":
            continue
        for row in source.get("rows") or []:
            raw = row.get("raw") or {}
            # The public shop card itself renders code "000" as UZS.  Do not
            # generalize this mapping to auction/tender/selection sources.
            if raw.get("currency") != "000" or above_threshold(row.get("amount"), "UZS") is not True:
                continue
            matches = name_candidates(registry, row.get("winner_name"))
            if matches:
                selected.append({"archive_row": row, "name_candidates": matches})
    return selected


def _localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("ru") or value.get("uz") or value.get("cyrl") or "")
    return str(value or "")


def summarize_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the fields needed for identity and keyword review."""
    order = item.get("order") or {}
    product = order.get("product_log") or {}
    classifier = product.get("classifier") or {}
    return {
        "procedure_id": str(item.get("id") or ""),
        "contract_number": str(item.get("number") or ""),
        "winner_name": _localized(item.get("producer", {}).get("title")),
        "winner_inn": normalize_inn((item.get("producer") or {}).get("tin")),
        "buyer_name": _localized((item.get("customer") or {}).get("title")),
        "amount": item.get("price"),
        "currency": "UZS",  # displayed by the public shop-card UI for this source
        "lot_number": str(order.get("lot_number") or ""),
        "classifier_code": classifier.get("code"),
        "classifier_title": _localized(classifier.get("title_ru") or classifier.get("title_uz")),
        "product_title": product.get("title"),
        "brand_title": product.get("brand_title"),
        "quantity": order.get("count"),
        "description": product.get("description"),
        "source_url": "https://ebirja.uz/ru/contracts/shop/%s" % item.get("id"),
    }


def enrich(candidates: List[Dict[str, Any]], max_details: int) -> List[Dict[str, Any]]:
    if max_details < 1:
        raise ValueError("max_details must be positive")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=30, headers=headers) as client:
        for candidate in candidates[:max_details]:
            archive_row = candidate["archive_row"]
            response = client.get(DETAIL_ENDPOINT, params={"id": archive_row.get("procedure_id")})
            response.raise_for_status()
            raw_bytes = response.content
            payload = response.json()
            detail = payload.get("result")
            if not isinstance(detail, dict):
                raise ValueError("unexpected Ebirja shop detail schema")
            results.append({"archive": {key: archive_row.get(key) for key in
                                        ("procedure_id", "contract_number", "awarded_at", "winner_name", "amount")},
                            "name_candidates": candidate["name_candidates"],
                            "detail_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                            "detail": summarize_detail(detail)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="JSON output of collect_ebirja_contract_api")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-details", type=int, default=25)
    args = parser.parse_args()
    registry = load_registry()
    snapshot = json.loads(Path(args.archive).read_text(encoding="utf-8"))
    candidates = candidate_rows(snapshot, registry)
    results = enrich(candidates, args.max_details)
    output = {"captured_at": datetime.now(timezone.utc).isoformat(), "mode": "public_api_read_only_bounded",
              "archive_complete": snapshot.get("complete") is True,
              "candidate_count": len(candidates), "fetched_count": len(results), "max_details": args.max_details,
              "results": results}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(target), "candidates": len(candidates), "fetched": len(results)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
