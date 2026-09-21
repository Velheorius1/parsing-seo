#!/usr/bin/env python3
"""Generate a safe weekly delta from a bounded competitor-award snapshot.

This is an offline reporting layer.  It sends no Telegram, reads no settings or
database, and refuses to advance a state file unless the input archive and all
candidate details are complete.  A scheduler may call it later only after a
separate production-review decision.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from crawler.core.competitor_audit import above_threshold, normalize_inn


def qualified_awards(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    awards = []
    for item in snapshot.get("results") or []:
        detail = item.get("detail") or {}
        winner_inn = normalize_inn(detail.get("winner_inn"))
        if winner_inn is None or above_threshold(detail.get("amount"), detail.get("currency")) is not True:
            continue
        key = "ebirja-shop:%s:%s" % (winner_inn, detail.get("contract_number") or detail.get("procedure_id"))
        awards.append({"key": key, "winner_inn": winner_inn, "winner_name": detail.get("winner_name"),
                       "contract_number": detail.get("contract_number"), "procedure_id": detail.get("procedure_id"),
                       "amount": detail.get("amount"), "currency": detail.get("currency"),
                       "title": detail.get("product_title"), "description": detail.get("description"),
                       "source_url": detail.get("source_url")})
    return awards


def snapshot_is_complete(snapshot: Dict[str, Any]) -> bool:
    return (snapshot.get("archive_complete") is True and
            snapshot.get("candidate_count") == snapshot.get("fetched_count") and
            isinstance(snapshot.get("results"), list))


def delta(snapshot: Dict[str, Any], prior_state: Dict[str, Any]) -> Dict[str, Any]:
    complete = snapshot_is_complete(snapshot)
    current = qualified_awards(snapshot)
    prior_keys = set(prior_state.get("award_keys") or [])
    return {"snapshot_complete": complete, "current_qualified_count": len(current),
            "prior_qualified_count": len(prior_keys),
            "new_awards": [award for award in current if award["key"] not in prior_keys],
            "state_candidate": {"award_keys": sorted(award["key"] for award in current),
                                "captured_at": snapshot.get("captured_at")}}


def _read(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def _write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--state", required=True, help="missing file means first-run baseline")
    parser.add_argument("--output", required=True)
    parser.add_argument("--advance-state", action="store_true", help="write state only if snapshot is complete")
    args = parser.parse_args()
    report = delta(_read(Path(args.snapshot), {}), _read(Path(args.state), {"award_keys": []}))
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["state_advanced"] = False
    if args.advance_state and report["snapshot_complete"]:
        _write(Path(args.state), report["state_candidate"])
        report["state_advanced"] = True
    _write(Path(args.output), report)
    print(json.dumps({"output": args.output, "snapshot_complete": report["snapshot_complete"],
                      "new_awards": len(report["new_awards"]), "state_advanced": report["state_advanced"]},
                     ensure_ascii=False))
    return 0 if report["snapshot_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
