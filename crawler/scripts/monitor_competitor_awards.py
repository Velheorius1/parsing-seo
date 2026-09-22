#!/usr/bin/env python3
"""Generate a safe weekly delta from a bounded competitor-award snapshot.

This is an offline reporting layer.  It sends no Telegram, reads no settings or
database, and refuses to advance a state file unless the input archive and all
candidate details are complete.  A scheduler may call it later only after a
separate production-review decision.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

from crawler.core.competitor_audit import above_threshold, normalize_inn


# Every weekly report must contain each of these rows.  ``winner_unobservable``
# and ``auth_required`` are useful outcomes: treating either as an empty list
# of awards would fabricate a zero-result claim.
SOURCE_PASSPORT = (
    ("etender_deals", "ETender UZEX Deals"),
    ("uzex_direct", "UZEX/Xarid Direct"),
    ("ebirja_shop", "Ebirja E-shop contracts"),
    ("ebirja_auction", "Ebirja auction contracts"),
    ("ebirja_tender", "Ebirja tender contracts"),
    ("ebirja_selection", "Ebirja selection contracts"),
    ("cooperation_contracts", "Cooperation contracts"),
    ("xt_xarid", "XT-Xarid public procedures"),
    ("hayotbirja", "Hayotbirja public procedures (XT mirror)"),
)


def _money(value: Any) -> str:
    try:
        return "{:,.0f}".format(float(value)).replace(",", " ")
    except (TypeError, ValueError):
        return str(value or "?")


def build_digest(report: Dict[str, Any]) -> str:
    """Build one compact retrospective digest (never an urgent lead alert)."""
    new_rows = report.get("new_awards") or []
    changed_rows = report.get("changed_awards") or []
    lines = ["🏆 Победы конкурентов за неделю", ""]
    for label, rows in (("Новые", new_rows), ("Изменения", changed_rows)):
        if not rows:
            continue
        lines.append("%s: %d" % (label, len(rows)))
        for row in rows[:10]:
            name = row.get("winner_name") or "ИНН %s" % row.get("winner_inn")
            title = str(row.get("title") or "без названия")[:100]
            lines.append("• %s · %s %s\n%s" % (name, _money(row.get("amount")),
                                                row.get("currency") or "", title))
            if row.get("source_url"):
                lines.append(str(row["source_url"]))
        if len(rows) > 10:
            lines.append("…и ещё %d" % (len(rows) - 10))
        lines.append("")
    lines.append("Ретроспективный мониторинг договоров; не открытый тендерный алерт.")
    return "\n".join(lines).strip()


def deliver_report(report: Dict[str, Any], sender) -> bool:
    """Silent baseline/empty run; otherwise require confirmed delivery."""
    if report.get("bootstrap") or not ((report.get("new_awards") or []) +
                                       (report.get("changed_awards") or [])):
        return True
    return bool(sender(build_digest(report)))


def _telegram_sender(text: str) -> bool:
    from crawler.config.settings import settings
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return False
    response = httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                          json={"chat_id": settings.telegram_alert_chat_id, "text": text,
                                "disable_web_page_preview": True}, timeout=30)
    return response.status_code == 200 and response.json().get("ok") is True


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


def _qualified_source_awards(source_id: str, source_run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate generic collector output before it can become a digest event."""
    awards = []
    for row in source_run.get("awards") or []:
        # UZEX collectors distinguish a participant from the actual winner.
        # A missing or false outcome is not sufficient evidence for a weekly
        # competitor-win report, even when the bidder, amount and contract ID
        # otherwise match every gate.
        if source_id in ("etender_deals", "uzex_direct") and row.get("is_win") is not True:
            continue
        # The bounded UZEX public collector emits audit-shaped records
        # (final_total/evidence_url).  The weekly monitor historically expected
        # manifest-shaped amount/source_url and silently discarded those real
        # wins at the price threshold.
        amount = row.get("amount")
        if amount is None:
            amount = row.get("final_total")
        source_url = row.get("source_url") or row.get("evidence_url")
        winner_inn = normalize_inn(row.get("winner_inn"))
        if winner_inn is None or above_threshold(amount, row.get("currency")) is not True:
            continue
        contract = row.get("contract_number") or row.get("award_id") or row.get("procedure_id")
        if not contract:
            continue
        event = dict(row)
        event["amount"] = amount
        event["source_url"] = source_url
        event["key"] = "%s:%s:%s" % (source_id, winner_inn, contract)
        event["winner_inn"] = winner_inn
        fingerprint = {key: event.get(key) for key in ("winner_inn", "contract_number", "award_id", "procedure_id",
                                                        "amount", "currency", "title", "status", "is_win")}
        event["content_hash"] = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True,
                                                            default=str).encode("utf-8")).hexdigest()
        awards.append(event)
    return awards


def multi_source_delta(source_runs: Dict[str, Dict[str, Any]], prior_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build an all-exchange report without advancing incomplete source state.

    ``source_runs`` is intentionally collector-neutral: ETender, Direct,
    Ebirja and Cooperation can supply their own receipts, while XT/Hayot can
    honestly report ``winner_unobservable``.  Absent sources are rendered as
    ``not_collected`` rather than silently omitted.
    """
    old = prior_state.get("sources") or {}
    bootstrap = not bool(old)
    statuses, new_awards, changed_awards, candidate_state = [], [], [], {}
    for source_id, label in SOURCE_PASSPORT:
        run = source_runs.get(source_id) or {"status": "not_collected"}
        status = str(run.get("status") or "not_collected")
        entry = {"source_id": source_id, "label": label, "status": status,
                 "detail": run.get("detail")}
        if status == "complete":
            current = _qualified_source_awards(source_id, run)
            previous = set((old.get(source_id) or {}).get("award_keys") or [])
            old_hashes = (old.get(source_id) or {}).get("content_hashes") or {}
            entry["qualified_awards"] = len(current)
            entry["new_awards"] = len([row for row in current if row["key"] not in previous])
            entry["changed_awards"] = len([row for row in current if row["key"] in previous and
                                            old_hashes.get(row["key"]) != row["content_hash"]])
            if not bootstrap:
                new_awards.extend(row for row in current if row["key"] not in previous)
                changed_awards.extend(row for row in current if row["key"] in previous and
                                      old_hashes.get(row["key"]) != row["content_hash"])
            candidate_state[source_id] = {"award_keys": sorted(row["key"] for row in current),
                                          "content_hashes": {row["key"]: row["content_hash"] for row in current},
                                          "captured_at": run.get("captured_at")}
        else:
            entry["qualified_awards"] = None
            entry["new_awards"] = None
            # Preserve prior state on incomplete / unobservable / auth-required
            # runs so a temporary failure can never make an award look new/old.
            if source_id in old:
                candidate_state[source_id] = old[source_id]
        statuses.append(entry)
    return {"sources": statuses, "new_awards": new_awards, "changed_awards": changed_awards, "bootstrap": bootstrap,
            "state_candidate": {"sources": candidate_state},
            "all_sources_reported": len(statuses) == len(SOURCE_PASSPORT)}


def _read(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def _write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--snapshot", help="legacy bounded Ebirja snapshot")
    input_group.add_argument("--source-runs", help="all-exchange source-run manifest JSON")
    parser.add_argument("--state", required=True, help="missing file means first-run baseline")
    parser.add_argument("--output", required=True)
    parser.add_argument("--advance-state", action="store_true", help="write state only if snapshot is complete")
    parser.add_argument("--send-telegram", action="store_true", help="send non-empty non-bootstrap digest")
    args = parser.parse_args()
    state_path = Path(args.state)
    prior = _read(state_path, {"award_keys": []})
    if args.source_runs:
        report = multi_source_delta(_read(Path(args.source_runs), {}), prior)
        complete_sources = [row["source_id"] for row in report["sources"] if row["status"] == "complete"]
        report["state_safe_sources"] = complete_sources
        can_advance = bool(complete_sources)
    else:
        report = delta(_read(Path(args.snapshot), {}), prior)
        can_advance = report["snapshot_complete"]
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["state_advanced"] = False
    report["telegram_delivered"] = None
    if args.send_telegram:
        report["telegram_delivered"] = deliver_report(report, _telegram_sender)
        if not report["telegram_delivered"]:
            _write(Path(args.output), report)
            print(json.dumps({"output": args.output, "telegram_delivered": False,
                              "state_advanced": False}, ensure_ascii=False))
            return 3
    if args.advance_state and can_advance:
        _write(state_path, report["state_candidate"])
        report["state_advanced"] = True
    _write(Path(args.output), report)
    print(json.dumps({"output": args.output, "snapshot_complete": report.get("snapshot_complete"),
                      "all_sources_reported": report.get("all_sources_reported"),
                      "new_awards": len(report["new_awards"]), "state_advanced": report["state_advanced"]},
                     ensure_ascii=False))
    if args.source_runs:
        return 0 if report["all_sources_reported"] else 2
    return 0 if report["snapshot_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
