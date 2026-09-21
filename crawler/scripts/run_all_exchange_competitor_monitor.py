#!/usr/bin/env python3
"""Run the public, read-only competitor monitor across every tracked exchange.

The runner deliberately produces a source passport even where a public site
does not disclose a winner INN or a contract currency.  It neither touches
Supabase/state files nor sends Telegram; pass its JSON to
``monitor_competitor_awards`` for an offline delta preview.
"""
import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from crawler.scripts.collect_cooperation_contracts import collect as collect_cooperation
from crawler.scripts.collect_ebirja_contract_api import collect_source
from crawler.scripts.collect_uzex_award_api import collect as collect_uzex
from crawler.core.competitor_audit import load_registry


def _ebirja_run(source_key: str, date_from: date, page_size: int, page_cap: int) -> Dict[str, Any]:
    result = collect_source(source_key, date_from, page_size, page_cap)
    # Only the E-shop detail endpoint reveals the producer identity.  This
    # list collector is still useful for coverage receipts, but must not make
    # a winner claim before its bounded detail-enrichment step.
    if source_key == "shop":
        return {"status": "detail_enrichment_required", "captured_at": datetime.now(timezone.utc).isoformat(),
                "detail": "public archive collected; bounded producer-INN detail enrichment required",
                "receipt": result}
    return {"status": "complete_name_only", "captured_at": datetime.now(timezone.utc).isoformat(),
            "detail": "public contract list has no winner INN", "receipt": result}


def build_runs(date_from: date, page_size: int, page_cap: int) -> Dict[str, Dict[str, Any]]:
    """Collect every public source once, retaining limitations explicitly."""
    captured = datetime.now(timezone.utc).isoformat()
    runs = {}  # type: Dict[str, Dict[str, Any]]
    for key, source_id in (("deals", "etender_deals"), ("direct", "uzex_direct")):
        try:
            result = collect_uzex(key, date_from, page_size, page_cap)
            runs[source_id] = {"status": "complete" if result["complete"] else "incomplete",
                               "captured_at": result["captured_at"], "awards": result["awards"],
                               "detail": result["completion"], "receipt": result}
        except Exception as exc:
            runs[source_id] = {"status": "collector_error", "captured_at": captured, "detail": str(exc)[:180]}
    for source_key, source_id in (("shop", "ebirja_shop"), ("auction", "ebirja_auction"),
                                  ("tender", "ebirja_tender"), ("selection", "ebirja_selection")):
        try:
            runs[source_id] = _ebirja_run(source_key, date_from, page_size, page_cap)
        except Exception as exc:
            runs[source_id] = {"status": "collector_error", "captured_at": captured, "detail": str(exc)[:180]}
    try:
        cooperation = collect_cooperation(date_from, page_size, page_cap, load_registry())
        runs["cooperation_contracts"] = {"status": "currency_unobservable", "captured_at": captured,
                                          "detail": "public registry omits contract currency", "receipt": cooperation}
    except Exception as exc:
        runs["cooperation_contracts"] = {"status": "collector_error", "captured_at": captured, "detail": str(exc)[:180]}
    runs["xt_xarid"] = {"status": "winner_unobservable", "captured_at": captured,
                         "detail": "public RPC exposes procedure fields but not winner INN"}
    runs["hayotbirja"] = {"status": "mirror", "captured_at": captured,
                           "detail": "Hayotbirja mirrors XT-Xarid; public RPC omits winner INN"}
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", help="YYYY-MM-DD; default is a 14-day overlap")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--page-cap", type=int, default=3, help="bounded per source; defaults to 3")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.page_size < 1 or args.page_cap < 1:
        parser.error("page size and page cap must be positive")
    lower = date.fromisoformat(args.date_from) if args.date_from else date.today() - timedelta(days=14)
    result = {"mode": "public_read_only_all_exchange_run", "captured_at": datetime.now(timezone.utc).isoformat(),
              "date_from": lower.isoformat(), "sources": build_runs(lower, args.page_size, args.page_cap)}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result["sources"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output), "sources": len(result["sources"]),
                      "statuses": {key: row["status"] for key, row in result["sources"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
