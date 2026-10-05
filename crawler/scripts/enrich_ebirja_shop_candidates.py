#!/usr/bin/env python3
"""Enrich bounded Ebirja name-candidates with their public contract card.

The archive list omits supplier INN and product details.  This tool first
selects only review candidates from a previously captured archive and then
fetches at most ``--max-details`` public cards.  It never searches the whole
archive live, and it never writes Supabase or Telegram.

С 05.10.2026 — не только э-магазин: у аукционов, тендеров и отборов тоже есть
публичная карточка с ИНН поставщика (`external-auction-view`,
`external-tender-view`). До этого эти три списка монитор только помечал «имя без
ИНН» и не смотрел — и мимо прошли PREMIUM POLIGRAF BIZNES (книги кассира, 344 млн,
аукцион 28.09) и EDU PRESS (632 млн, отбор 09.09), обе фирмы из списка.
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
DETAIL_ENDPOINTS = {
    "shop": DETAIL_ENDPOINT,
    "auction": "https://xarid-api.ebirja.uz/common/contract/external-auction-view",
    "tender": "https://xarid-api.ebirja.uz/common/contract/external-tender-view",
    "selection": "https://xarid-api.ebirja.uz/common/contract/external-tender-view",
}
# Публичная карточка на сайте — та, что открывается человеку (проверено 05.10).
CARD_URL = "https://ebirja.uz/ru/contracts/%s/%s"


def candidate_rows(snapshot: Dict[str, Any], registry: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Select only high-value rows whose public winner name needs review."""
    selected = []
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else [snapshot]
    for source in sources:
        source_key = source.get("source_key")
        if source_key not in DETAIL_ENDPOINTS:
            continue
        for row in source.get("rows") or []:
            raw = row.get("raw") or {}
            # Код "000" публичная карточка показывает как UZS — у э-магазина и, по
            # сверке карточек 05.10, у аукциона, тендера и отбора тоже. Другой
            # код — валюту не угадываем, порог в сумах к нему не применим.
            if raw.get("currency") != "000" or above_threshold(row.get("amount"), "UZS") is not True:
                continue
            matches = name_candidates(registry, row.get("winner_name"))
            if matches:
                selected.append({"source_key": source_key, "archive_row": row, "name_candidates": matches})
    return selected


def _localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("ru") or value.get("uz") or value.get("cyrl") or "")
    return str(value or "")


def _procedure_summary(item: Dict[str, Any], source_key: str) -> Dict[str, Any]:
    """Аукцион/тендер/отбор: предмет — названия классификаторов лота."""
    proc = item.get("auction") if source_key == "auction" else item.get("tender")
    proc = proc if isinstance(proc, dict) else {}
    lines = proc.get("auction_classifiers") or proc.get("tender_classifiers") or []
    titles = []
    for line in lines:
        cls = (line or {}).get("classifier") or {}
        title = str(cls.get("title_ru") or cls.get("title_uz") or "").strip()
        if title and title not in titles:
            titles.append(title)
    return {
        "procedure_id": str(item.get("id") or ""),
        "contract_number": str(item.get("number") or ""),
        "awarded_at": item.get("created_at"),
        "winner_name": _localized((item.get("producer") or {}).get("title")),
        "winner_inn": normalize_inn((item.get("producer") or {}).get("tin")),
        "buyer_name": _localized((item.get("customer") or {}).get("title")),
        "amount": item.get("price"),
        "currency": "UZS",  # код "000", карточка показывает UZS (см. candidate_rows)
        "lot_number": str(proc.get("lot") or ""),
        "title": ", ".join(titles)[:180] or None,
        "source_url": CARD_URL % (source_key, item.get("id")),
    }


def summarize_detail(item: Dict[str, Any], source_key: str = "shop") -> Dict[str, Any]:
    """Keep only the fields needed for identity and keyword review."""
    if source_key != "shop":
        return _procedure_summary(item, source_key)
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
            source_key = candidate.get("source_key") or "shop"
            response = client.get(DETAIL_ENDPOINTS[source_key], params={"id": archive_row.get("procedure_id")})
            response.raise_for_status()
            raw_bytes = response.content
            payload = response.json()
            detail = payload.get("result")
            if not isinstance(detail, dict):
                raise ValueError("unexpected Ebirja %s detail schema" % source_key)
            results.append({"archive": {key: archive_row.get(key) for key in
                                        ("procedure_id", "contract_number", "awarded_at", "winner_name", "amount")},
                            "source_key": source_key,
                            "name_candidates": candidate["name_candidates"],
                            "detail_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                            "detail": summarize_detail(detail, source_key)})
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
