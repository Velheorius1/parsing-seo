#!/usr/bin/env python3
"""Build an all-exchange weekly source manifest from collector artifacts.

This composes existing read-only outputs; it makes no network requests and
never emits Telegram.  Source rows without public winner/currency evidence are
explicitly retained with their limitation status.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _jsonl(path: str) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _award_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"winner_inn": row.get("winner_inn"), "winner_name": row.get("winner_name"),
             "amount": row.get("final_total"), "currency": row.get("currency"),
             "award_id": row.get("award_id"), "procedure_id": row.get("procedure_id"),
             "contract_number": row.get("contract_id"), "title": row.get("title"),
             "source_url": row.get("evidence_url"), "is_win": True} for row in rows
            if row.get("is_win") is True and row.get("above_threshold") is True]


def _ebirja_rows(detail_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item.get("detail") or {} for item in detail_snapshot.get("results") or []]


def build(etender_rows: List[Dict[str, Any]], direct_rows: List[Dict[str, Any]],
          ebirja_detail: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Produce all required source statuses for one completed evidence bundle."""
    captured = datetime.now(timezone.utc).isoformat()
    ebirja_complete = ebirja_detail.get("archive_complete") is True and (
        ebirja_detail.get("candidate_count") == ebirja_detail.get("fetched_count"))
    return {
        "etender_deals": {"status": "complete", "captured_at": captured, "awards": _award_rows(etender_rows)},
        "uzex_direct": {"status": "complete", "captured_at": captured, "awards": _award_rows(direct_rows)},
        "ebirja_shop": {"status": "complete" if ebirja_complete else "incomplete",
                        "captured_at": captured, "awards": _ebirja_rows(ebirja_detail)},
        "ebirja_auction": {"status": "complete_name_only", "detail": "public list has no winner INN"},
        "ebirja_tender": {"status": "complete_name_only", "detail": "public list has no winner INN"},
        "ebirja_selection": {"status": "complete_name_only", "detail": "public list has no winner INN"},
        "cooperation_contracts": {"status": "currency_unobservable",
                                  "detail": "public registry omits contract currency"},
        "xt_xarid": {"status": "winner_unobservable", "detail": "public RPC omits winner INN"},
        "hayotbirja": {"status": "mirror", "detail": "XT-Xarid mirror; public RPC omits winner INN"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--etender-awards", required=True)
    parser.add_argument("--direct-awards", required=True)
    parser.add_argument("--ebirja-details", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build(_jsonl(args.etender_awards), _jsonl(args.direct_awards),
                   json.loads(Path(args.ebirja_details).read_text(encoding="utf-8")))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(target), "sources": len(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
