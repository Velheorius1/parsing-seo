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

import httpx

from crawler.scripts.collect_cooperation_contracts import collect as collect_cooperation
from crawler.scripts.collect_ebirja_contract_api import collect_source
from crawler.scripts.enrich_ebirja_shop_candidates import candidate_rows, enrich
from crawler.scripts.enrich_competitor_award_specs import enrich_awards
from crawler.scripts.collect_uzex_award_api import collect as collect_uzex
from crawler.core.competitor_audit import entity_for_inn, load_registry, normalize_inn


_PUBLIC_RPC_ENDPOINTS = (
    ("xt_xarid", "https://api.xt-xarid.uz/rpc"),
    ("hayotbirja", "https://api.hayotbirja.uz/rpc"),
)


def _public_rpc_probe(url: str, post) -> Dict[str, Any]:
    """Make one bounded schema probe without claiming winner visibility."""
    response = post(
        url,
        json={"jsonrpc": "2.0", "method": "ref", "id": 1,
              "params": {"ref": "ref_tender_public", "op": "read", "limit": 5, "offset": 0}},
        headers={"Content-Type": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error") is not None:
        raise ValueError("public RPC returned an error payload")
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise ValueError("public RPC result is not a list")
    sample_ids = [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id") is not None]
    return {"observed_at": datetime.now(timezone.utc).isoformat(),
            "http_status": getattr(response, "status_code", None),
            "rpc_ref": "ref_tender_public", "row_count": len(rows),
            "sample_ids": sample_ids}


def _public_rpc_runs(post=None) -> Dict[str, Dict[str, Any]]:
    """Return honest live passport rows for XT-Xarid and its Hayot mirror."""
    requester = post or httpx.post
    observations = {}  # type: Dict[str, Dict[str, Any]]
    errors = {}  # type: Dict[str, str]
    for source_id, url in _PUBLIC_RPC_ENDPOINTS:
        try:
            observations[source_id] = _public_rpc_probe(url, requester)
        except Exception as exc:
            errors[source_id] = "%s: %s" % (type(exc).__name__, str(exc)[:140])

    xt_observation = observations.get("xt_xarid")
    hayot_observation = observations.get("hayotbirja")
    if xt_observation is None:
        xt = {"status": "collector_error", "captured_at": datetime.now(timezone.utc).isoformat(),
              "detail": errors.get("xt_xarid") or "public RPC was not observed"}
    else:
        xt = {"status": "winner_unobservable", "captured_at": xt_observation["observed_at"],
              "detail": "fresh public RPC schema observed; winner INN is not exposed",
              "receipt": xt_observation}

    if hayot_observation is None:
        hayot = {"status": "collector_error", "captured_at": datetime.now(timezone.utc).isoformat(),
                 "detail": errors.get("hayotbirja") or "public RPC was not observed"}
    else:
        sample_match = (xt_observation is not None and
                        hayot_observation["sample_ids"] == xt_observation["sample_ids"])
        receipt = dict(hayot_observation)
        receipt["mirror_sample_match"] = sample_match
        hayot = {"status": "mirror" if sample_match else "mirror_unconfirmed",
                 "captured_at": hayot_observation["observed_at"],
                 "detail": ("fresh public RPC sample matches XT-Xarid; winner INN is not exposed"
                            if sample_match else "public RPC observed but XT-Xarid mirror sample was not confirmed"),
                 "receipt": receipt}
    return {"xt_xarid": xt, "hayotbirja": hayot}


def _exact_ebirja_awards(details, registry):
    # type: (List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]) -> (List[Dict[str, Any]], int, int)
    """Keep Ebirja Shop wins only after the detail INN joins the registry.

    Name matching selects a bounded detail-card queue; it is never evidence of
    identity. A valid-looking but unregistered INN is a confirmed rejection;
    an absent or malformed INN is unresolved and must not erase prior history.
    """
    awards, rejected, unresolved = [], 0, 0
    for entry in details:
        detail = entry.get("detail") if isinstance(entry, dict) else None
        if not isinstance(detail, dict):
            unresolved += 1
            continue
        winner_inn = normalize_inn(detail.get("winner_inn"))
        if winner_inn is None:
            unresolved += 1
            continue
        entity = entity_for_inn(registry, winner_inn)
        if entity is None:
            rejected += 1
            continue
        award = dict(detail)
        award["competitor"] = entity["name"]
        awards.append(award)
    return awards, rejected, unresolved


def _ebirja_run(source_key: str, date_from: date, page_size: int, page_cap: int,
                registry: Dict[str, List[Dict[str, Any]]], max_details: int) -> Dict[str, Any]:
    result = collect_source(source_key, date_from, page_size, page_cap)
    # Only the E-shop detail endpoint reveals the producer identity.  This
    # list collector is still useful for coverage receipts, but must not make
    # a winner claim before its bounded detail-enrichment step.
    if source_key == "shop":
        candidates = candidate_rows({"sources": [result]}, registry)
        details = enrich(candidates, max_details)
        complete = result["complete"] and len(candidates) == len(details)
        awards, identity_rejected, identity_unresolved = _exact_ebirja_awards(details, registry)
        status = "complete" if complete else "incomplete"
        if complete and identity_unresolved:
            status = "partial_identity"
        return {"status": status, "captured_at": datetime.now(timezone.utc).isoformat(),
                "detail": result["completion"], "awards": awards,
                "receipt": result, "candidate_count": len(candidates), "fetched_count": len(details),
                "identity_rejected_count": identity_rejected,
                "identity_unresolved_count": identity_unresolved}
    status = "complete_name_only" if result["complete"] else "incomplete_name_only"
    return {"status": status, "captured_at": datetime.now(timezone.utc).isoformat(),
            "detail": ("public contract list has no winner INN; %s" % result["completion"]),
            "receipt": result}


def build_runs(date_from: date, page_size: int, page_cap: int, max_details: int = 25,
               rpc_post=None) -> Dict[str, Dict[str, Any]]:
    """Collect every public source once, retaining limitations explicitly."""
    captured = datetime.now(timezone.utc).isoformat()
    runs = {}  # type: Dict[str, Dict[str, Any]]
    with httpx.Client(timeout=20) as detail_client:
        for key, source_id in (("deals", "etender_deals"), ("direct", "uzex_direct")):
            try:
                result = collect_uzex(key, date_from, page_size, page_cap)
                awards, detail_enrichment = enrich_awards(source_id, result["awards"], detail_client.get,
                                                          max_details=max_details)
                runs[source_id] = {"status": "complete" if result["complete"] else "incomplete",
                                   "captured_at": result["captured_at"], "awards": awards,
                                   "detail": result["completion"], "detail_enrichment": detail_enrichment,
                                   "receipt": result}
            except Exception as exc:
                runs[source_id] = {"status": "collector_error", "captured_at": captured, "detail": str(exc)[:180]}
    registry = load_registry()
    for source_key, source_id in (("shop", "ebirja_shop"), ("auction", "ebirja_auction"),
                                  ("tender", "ebirja_tender"), ("selection", "ebirja_selection")):
        try:
            runs[source_id] = _ebirja_run(source_key, date_from, page_size, page_cap, registry, max_details)
        except Exception as exc:
            runs[source_id] = {"status": "collector_error", "captured_at": captured, "detail": str(exc)[:180]}
    try:
        cooperation = collect_cooperation(date_from, page_size, page_cap, registry)
        status = "currency_unobservable" if cooperation.get("complete") else "incomplete_currency_unobservable"
        runs["cooperation_contracts"] = {"status": status, "captured_at": captured,
                                          "detail": "public registry omits contract currency; %s" % cooperation.get("completion"),
                                          "receipt": cooperation}
    except Exception as exc:
        runs["cooperation_contracts"] = {"status": "collector_error", "captured_at": captured, "detail": str(exc)[:180]}
    runs.update(_public_rpc_runs(rpc_post))
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", help="YYYY-MM-DD; default is a 14-day overlap")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--page-cap", type=int, default=3, help="bounded per source; defaults to 3")
    parser.add_argument("--max-details", type=int, default=25, help="bounded Ebirja Shop detail cards")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.page_size < 1 or args.page_cap < 1:
        parser.error("page size and page cap must be positive")
    lower = date.fromisoformat(args.date_from) if args.date_from else date.today() - timedelta(days=14)
    result = {"mode": "public_read_only_all_exchange_run", "captured_at": datetime.now(timezone.utc).isoformat(),
              "date_from": lower.isoformat(), "sources": build_runs(lower, args.page_size, args.page_cap, args.max_details)}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result["sources"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output), "sources": len(result["sources"]),
                      "statuses": {key: row["status"] for key, row in result["sources"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
