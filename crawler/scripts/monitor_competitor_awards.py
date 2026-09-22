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

# These are the only statuses that make an all-exchange receipt meaningful:
# completed data or an explicitly permanent public limitation. A synthesized
# passport row is not itself evidence that the collector actually ran.
_REPORTED_STATUSES = frozenset((
    "complete", "complete_name_only", "currency_unobservable",
    "winner_unobservable", "mirror",
))
_TELEGRAM_SAFE_CHARS = 3500


def _money(value: Any) -> str:
    try:
        return "{:,.0f}".format(float(value)).replace(",", " ")
    except (TypeError, ValueError):
        return str(value or "?")


def _award_block(row: Dict[str, Any]) -> str:
    name = str(row.get("winner_name") or "ИНН %s" % row.get("winner_inn"))[:140]
    title = str(row.get("title") or "без названия")[:180]
    lines = ["• %s · %s %s\n%s" % (name, _money(row.get("amount")), row.get("currency") or "", title)]
    specification = " ".join(str(row.get("specification_text") or "").split())[:280]
    if specification:
        lines.append("Позиции: %s" % specification)
    if row.get("source_url"):
        # A malformed URL must not make one report exceed Telegram's hard cap.
        lines.append(str(row["source_url"])[:700])
    return "\n".join(lines)


def digest_batches(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bound the digest while retaining the exact award keys in each message."""
    batches, lines, keys, has_entries = [], ["🏆 Победы конкурентов за неделю"], [], False
    for label, rows in (("Новые", report.get("new_awards") or []),
                        ("Изменения", report.get("changed_awards") or [])):
        for row in rows:
            key = str(row.get("key") or "")
            entry = "%s:\n%s" % (label, _award_block(row))
            if has_entries and len("\n\n".join(lines + [entry])) > _TELEGRAM_SAFE_CHARS:
                batches.append({"text": "\n\n".join(lines), "award_keys": keys})
                lines, keys, has_entries = ["🏆 Победы конкурентов за неделю (продолжение)"], [], False
            # Every field is bounded above; keep the final guard for unusual Unicode.
            lines.append(entry[:_TELEGRAM_SAFE_CHARS - 100])
            has_entries = True
            if key:
                keys.append(key)
    if has_entries:
        lines.append("Ретроспективный мониторинг договоров; не открытый тендерный алерт.")
        batches.append({"text": "\n\n".join(lines)[:_TELEGRAM_SAFE_CHARS], "award_keys": keys})
    return batches


def build_digest(report: Dict[str, Any]) -> str:
    """Human-readable rendering of all delivery batches, for previews/tests."""
    return "\n\n".join(batch["text"] for batch in digest_batches(report))


def deliver_report(report: Dict[str, Any], sender, on_confirm=None) -> Dict[str, Any]:
    """Deliver bounded batches and checkpoint each unambiguous confirmation.

    ``sender`` may raise on a transport or malformed-response failure.  Those
    failures are delivery outcomes, not process failures: callers need the
    first confirmed batches in order to persist their recovery state.
    """
    if report.get("bootstrap") or not ((report.get("new_awards") or []) +
                                       (report.get("changed_awards") or [])):
        return {"complete": True, "delivered_keys": [], "failed_batch": None, "error": None}
    delivered = []
    for index, batch in enumerate(digest_batches(report)):
        try:
            sent = sender(batch["text"])
        except Exception as exc:
            return {"complete": False, "delivered_keys": delivered, "failed_batch": index,
                    "error": type(exc).__name__}
        if not sent:
            return {"complete": False, "delivered_keys": delivered, "failed_batch": index,
                    "error": "sender_returned_false"}
        try:
            if on_confirm is not None:
                on_confirm(batch["award_keys"])
        except Exception as exc:
            # Telegram may already have accepted the batch. Keep it pending
            # rather than fabricate a confirmation we failed to persist.
            return {"complete": False, "delivered_keys": delivered, "failed_batch": index,
                    "error": "checkpoint_%s" % type(exc).__name__}
        delivered.extend(batch["award_keys"])
    return {"complete": True, "delivered_keys": delivered, "failed_batch": None, "error": None}


def _delivery_buckets(value: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    # Keep a deliberately small, JSON-compatible outbox schema. The bucket
    # encodes whether a delayed record remains a new award or a correction.
    return {
        "new_awards": [dict(row) for row in (value.get("new_awards") or [])
                       if isinstance(row, dict) and row.get("key")],
        "changed_awards": [dict(row) for row in (value.get("changed_awards") or [])
                           if isinstance(row, dict) and row.get("key")],
    }


def merge_delivery_outbox(report: Dict[str, Any], pending: Dict[str, Any]) -> Dict[str, Any]:
    """Merge durable pending events into the current delivery report.

    Current evidence wins for a matching key; pending events absent from the
    new 30-day collection window stay deliverable until confirmed.
    """
    current = _delivery_buckets(report)
    pending_rows = _delivery_buckets(pending)
    current_keys = {row["key"] for rows in current.values() for row in rows}
    merged = {bucket: list(rows) for bucket, rows in current.items()}
    for bucket, rows in pending_rows.items():
        merged[bucket].extend(row for row in rows if row["key"] not in current_keys)
    result = dict(report)
    result.update(merged)
    return result


def outbox_after_delivery(outbox: Dict[str, Any], delivered_keys: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Return the durable retry queue after removing confirmed batch keys."""
    delivered = set(delivered_keys)
    buckets = _delivery_buckets(outbox)
    return {bucket: [row for row in rows if row["key"] not in delivered]
            for bucket, rows in buckets.items()}


def prepare_delivery_outbox(report: Dict[str, Any], pending: Dict[str, Any],
                            send_telegram: bool) -> Any:
    """Build an outbox only for a real, non-bootstrap delivery attempt."""
    if not send_telegram or report.get("bootstrap"):
        return None
    merged = merge_delivery_outbox(report, pending)
    if not ((merged.get("new_awards") or []) + (merged.get("changed_awards") or [])):
        return None
    return merged


def state_after_delivery(prior_state: Dict[str, Any], candidate: Dict[str, Any],
                         delivered_keys: List[str]) -> Dict[str, Any]:
    """Advance only confirmed events after a partial Telegram failure."""
    delivered = set(delivered_keys)
    if "sources" not in candidate:
        prior_keys = set(prior_state.get("award_keys") or [])
        current_keys = set(candidate.get("award_keys") or [])
        return {"award_keys": sorted(prior_keys | (delivered & current_keys)),
                "captured_at": candidate.get("captured_at")}
    prior_sources = prior_state.get("sources") or {}
    out = {}  # type: Dict[str, Any]
    for source_id, current in (candidate.get("sources") or {}).items():
        old = prior_sources.get(source_id) or {}
        source_delivered = {key for key in delivered if key.startswith(source_id + ":")}
        old_keys = set(old.get("award_keys") or [])
        current_keys = set(current.get("award_keys") or [])
        hashes = dict(old.get("content_hashes") or {})
        for key in source_delivered & current_keys:
            hashes[key] = (current.get("content_hashes") or {}).get(key)
        out[source_id] = {"award_keys": sorted(old_keys | (source_delivered & current_keys)),
                          "content_hashes": hashes,
                          "captured_at": current.get("captured_at") or old.get("captured_at")}
    for source_id, old in prior_sources.items():
        if source_id not in out:
            out[source_id] = old
    return {"sources": out}


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
        # Ebirja shop detail has product_title/description instead of the
        # generic award title/specification fields. Reuse that already fetched
        # evidence rather than making another public-card request.
        event["title"] = event.get("title") or event.get("product_title")
        event["specification_text"] = event.get("specification_text") or event.get("description")
        event["key"] = "%s:%s:%s" % (source_id, winner_inn, contract)
        event["winner_inn"] = winner_inn
        fingerprint = {key: event.get(key) for key in ("winner_inn", "contract_number", "award_id", "procedure_id",
                                                        "amount", "currency", "title", "status", "is_win",
                                                        "specification_text")}
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
        if status in ("complete", "partial_identity"):
            current = _qualified_source_awards(source_id, run)
            previous = set((old.get(source_id) or {}).get("award_keys") or [])
            old_hashes = (old.get(source_id) or {}).get("content_hashes") or {}
            # A transient/capped detail lookup is absence of evidence, not an
            # empty specification. Preserve the last confirmed fingerprint for
            # that exact award until a later run has authoritative detail again.
            # Other changes are delayed for this row by at most one healthy run,
            # which is safer than a weekly false correction alert.
            for row in current:
                if (row.get("_specification_status") in ("unavailable", "capped") and
                        row["key"] in previous):
                    row["content_hash"] = old_hashes.get(row["key"])
                    row["content_change_deferred"] = "specification_unavailable"
            entry["qualified_awards"] = len(current)
            entry["new_awards"] = len([row for row in current if row["key"] not in previous])
            entry["changed_awards"] = len([row for row in current if row["key"] in previous and
                                            old_hashes.get(row["key"]) != row["content_hash"]])
            if not bootstrap:
                new_awards.extend(row for row in current if row["key"] not in previous)
                changed_awards.extend(row for row in current if row["key"] in previous and
                                      old_hashes.get(row["key"]) != row["content_hash"])
            if status == "partial_identity":
                # Exact winners are usable evidence, but unresolved cards mean
                # this snapshot cannot prove that an older award disappeared.
                candidate_keys = previous | {row["key"] for row in current}
                candidate_hashes = dict(old_hashes)
                candidate_hashes.update({row["key"]: row["content_hash"] for row in current})
            else:
                candidate_keys = {row["key"] for row in current}
                candidate_hashes = {row["key"]: row["content_hash"] for row in current}
            candidate_state[source_id] = {"award_keys": sorted(candidate_keys),
                                          "content_hashes": candidate_hashes,
                                          "captured_at": run.get("captured_at")}
        else:
            entry["qualified_awards"] = None
            entry["new_awards"] = None
            # Preserve prior state on incomplete / unobservable / auth-required
            # runs so a temporary failure can never make an award look new/old.
            if source_id in old:
                candidate_state[source_id] = old[source_id]
        statuses.append(entry)
    all_sources_reported = all(row["status"] in _REPORTED_STATUSES for row in statuses)
    return {"sources": statuses, "new_awards": new_awards, "changed_awards": changed_awards, "bootstrap": bootstrap,
            "state_candidate": {"sources": candidate_state},
            "all_sources_reported": all_sources_reported}


def _read(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def _write(path: Path, value: Dict[str, Any]) -> None:
    """Atomically replace a JSON receipt/state/outbox file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--snapshot", help="legacy bounded Ebirja snapshot")
    input_group.add_argument("--source-runs", help="all-exchange source-run manifest JSON")
    parser.add_argument("--state", required=True, help="missing file means first-run baseline")
    parser.add_argument("--output", required=True)
    parser.add_argument("--outbox", help="durable retry queue; defaults beside --state when sending")
    parser.add_argument("--advance-state", action="store_true", help="write state only if snapshot is complete")
    parser.add_argument("--send-telegram", action="store_true", help="send non-empty non-bootstrap digest")
    args = parser.parse_args()
    state_path = Path(args.state)
    prior = _read(state_path, {"award_keys": []})
    if args.source_runs:
        report = multi_source_delta(_read(Path(args.source_runs), {}), prior)
        complete_sources = [row["source_id"] for row in report["sources"]
                            if row["status"] in ("complete", "partial_identity")]
        report["state_safe_sources"] = complete_sources
        can_advance = bool(complete_sources)
    else:
        report = delta(_read(Path(args.snapshot), {}), prior)
        can_advance = report["snapshot_complete"]
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["state_advanced"] = False
    report["telegram_delivered"] = None
    delivery = {"complete": True, "delivered_keys": [], "failed_batch": None, "error": None}
    if args.send_telegram:
        outbox_path = Path(args.outbox) if args.outbox else state_path.with_name(state_path.name + ".outbox.json")
        pending = _read(outbox_path, {"new_awards": [], "changed_awards": []})
        delivery_report = prepare_delivery_outbox(report, pending, send_telegram=True)
        if delivery_report is not None:
            # Persist before the first Telegram call. A later weekly window can
            # be empty and must not make an unconfirmed award disappear.
            report = delivery_report
            outbox = _delivery_buckets(report)
            _write(outbox_path, outbox)
            checkpoint_state = prior

            def _checkpoint(keys):
                # State first: if the subsequent outbox write fails, the next
                # run can at worst duplicate a known message, never discard it.
                nonlocal checkpoint_state, outbox
                if args.advance_state and can_advance:
                    checkpoint_state = state_after_delivery(checkpoint_state, report["state_candidate"], keys)
                    _write(state_path, checkpoint_state)
                    report["state_advanced"] = True
                outbox = outbox_after_delivery(outbox, keys)
                _write(outbox_path, outbox)

            delivery = deliver_report(report, _telegram_sender, on_confirm=_checkpoint)
        else:
            delivery = deliver_report(report, _telegram_sender)
        report["telegram_delivered"] = delivery["complete"]
        report["telegram_delivery"] = delivery
    if args.advance_state and can_advance:
        no_events = not ((report.get("new_awards") or []) + (report.get("changed_awards") or []))
        if delivery["complete"] or report.get("bootstrap") or no_events:
            next_state = report["state_candidate"]
        elif delivery["delivered_keys"] and not report["state_advanced"]:
            next_state = state_after_delivery(prior, report["state_candidate"], delivery["delivered_keys"])
        else:
            next_state = None
        # Per-batch checkpoints retain prior keys during a partial delivery.
        # A fully confirmed run may now replace that checkpoint with the exact
        # current candidate, including legitimate expirations from the window.
        if next_state is not None and (delivery["complete"] or not report["state_advanced"]):
            _write(state_path, next_state)
            report["state_advanced"] = True
    _write(Path(args.output), report)
    print(json.dumps({"output": args.output, "snapshot_complete": report.get("snapshot_complete"),
                      "all_sources_reported": report.get("all_sources_reported"),
                      "new_awards": len(report["new_awards"]), "state_advanced": report["state_advanced"]},
                     ensure_ascii=False))
    if args.send_telegram and not delivery["complete"]:
        return 3
    if args.source_runs:
        return 0 if report["all_sources_reported"] else 2
    return 0 if report["snapshot_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
