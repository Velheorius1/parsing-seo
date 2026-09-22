#!/usr/bin/env python3
"""Recover specification text for bounded source/ID ranges after a cursor gap.

Dry-run is the default.  ``--apply`` updates only search_text and extra_info for
rows found in the requested ranges and requires an explicit backup path.
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import httpx

from crawler.adapters.api import _resolve_wildcard_path, _safe_str
from crawler.core.db import _get_client, _merge_detail_text, query_with_retry
from crawler.core.runner import load_sources


FIELDS = (
    "id,external_id,source,title,organization,search_text,extra_info,price,currency,"
    "deadline,date_start,date_end,collected_at,message_type,bid_count,status,source_url,"
    "alert_seq,relevance_score,group_id"
)


def _write(path, value):
    # type: (Path, Any) -> None
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def parse_gap(value):
    # type: (str) -> Tuple[str, int, int]
    try:
        source, low, high = value.rsplit(":", 2)
        low_value, high_value = int(low), int(high)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("gap must be SOURCE:LOW_EXCLUSIVE:HIGH_INCLUSIVE")
    if not source or low_value < 0 or high_value <= low_value:
        raise argparse.ArgumentTypeError("gap boundaries are invalid")
    return source, low_value, high_value


def candidate_ids(low_exclusive, high_inclusive):
    # type: (int, int) -> List[str]
    return [str(value) for value in range(low_exclusive + 1, high_inclusive + 1)]


def detail_id_for_row(row, wanted_ids):
    # type: (Dict[str, Any], Iterable[str]) -> Any
    """Map a persisted row back to the raw ID used by its detail endpoint."""
    wanted = set(str(value) for value in wanted_ids)
    external_id = str(row.get("external_id") or "")
    if external_id in wanted:
        return external_id
    numbers = re.findall(r"/(\d+)(?:/0)?(?:[/?#]|$)", str(row.get("source_url") or ""))
    for value in reversed(numbers):
        if value in wanted:
            return value
    return None


def load_targets(path):
    # type: (str) -> List[Tuple[str, str]]
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = value.get("targets") if isinstance(value, dict) else value
    targets = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("source") or ""), str(row.get("detail_id") or ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        targets.append(key)
    if not targets:
        raise ValueError("targets manifest has no source/detail_id rows")
    return targets


def fetch_rows(client, source, ids):
    # type: (Any, str, List[str]) -> List[Dict[str, Any]]
    wanted = set(str(value) for value in ids)
    rows = []  # type: List[Dict[str, Any]]
    found = set()
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        result = query_with_retry(
            lambda c=chunk: (client.table("tenders").select(FIELDS).eq("source", source)
                             .in_("external_id", c).execute()),
            label="detail recovery exact %s" % source,
        )
        for row in result.data or []:
            detail_id = detail_id_for_row(row, wanted)
            if detail_id and detail_id not in found:
                value = dict(row)
                value["_recovery_detail_id"] = detail_id
                rows.append(value)
                found.add(detail_id)

    # ETender persists display_no as external_id while detail uses the raw ID
    # visible only in source_url. Sweep this one source in bounded pages to map
    # the exact manifest IDs; never infer or reconstruct rows that are absent.
    offset = 0
    page_size = 1000
    while found != wanted:
        result = query_with_retry(
            lambda o=offset: (client.table("tenders").select(FIELDS).eq("source", source)
                              .order("collected_at", desc=True)
                              .range(o, o + page_size - 1).execute()),
            label="detail recovery source scan %s p%d" % (source, offset // page_size),
        )
        page = result.data or []
        for row in page:
            detail_id = detail_id_for_row(row, wanted - found)
            if detail_id and detail_id not in found:
                value = dict(row)
                value["_recovery_detail_id"] = detail_id
                rows.append(value)
                found.add(detail_id)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def detail_text(payload, config):
    # type: (Dict[str, Any], Any) -> str
    data = dict(payload or {})
    for field in config.detail_fetch.json_string_fields:
        value = data.get(field)
        if isinstance(value, str) and value.strip().startswith(("[", "{")):
            try:
                data[field] = json.loads(value, strict=False)
            except ValueError:
                pass
    parts = []
    for path in config.detail_fetch.text_fields:
        for value in _resolve_wildcard_path(data, path):
            text = _safe_str(value)
            if text and text not in parts:
                parts.append(text)
    return " ".join(parts)[:2000]


def update_payload(row, recovered_text, recovered_at):
    # type: (Dict[str, Any], str, str) -> Dict[str, Any]
    extra = dict(row.get("extra_info") or {})
    extra["_detail_text"] = recovered_text
    extra["_detail_recovered_at"] = recovered_at
    return {
        "extra_info": extra,
        "search_text": _merge_detail_text(row.get("search_text") or "", recovered_text),
    }


def recover(gaps, config_path, apply_changes=False, backup_path=None, exact_targets=None):
    # type: (Iterable[Tuple[str, int, int]], str, bool, Any, Any) -> Dict[str, Any]
    if apply_changes and backup_path is None:
        raise ValueError("--backup is required with --apply")
    configs = {source.name: source for source in load_sources(config_path) if source.detail_fetch}
    client = _get_client()
    recovered_at = datetime.now(timezone.utc).isoformat()
    receipt_rows, originals, updates = [], [], []
    work = []
    if exact_targets:
        by_source = {}  # type: Dict[str, List[str]]
        for source, detail_id in exact_targets:
            by_source.setdefault(source, []).append(detail_id)
        work = [(source, ids, None, None, True) for source, ids in by_source.items()]
    else:
        work = [(source, candidate_ids(low, high), low, high, False)
                for source, low, high in gaps]
    with httpx.Client(follow_redirects=True) as http:
        for source, ids, low, high, is_exact in work:
            config = configs.get(source)
            if config is None:
                raise ValueError("source has no detail_fetch config: %s" % source)
            rows = fetch_rows(client, source, ids)
            matched = set(str(row.get("_recovery_detail_id") or "") for row in rows)
            if is_exact:
                for detail_id in ids:
                    if detail_id not in matched:
                        receipt_rows.append({
                            "source": source, "detail_id": detail_id,
                            "external_id": None, "status": "missing_row",
                            "error": "not present in persisted source rows",
                        })
            for row in rows:
                extra = row.get("extra_info") or {}
                raw_detail_id = str(row.get("_recovery_detail_id") or row.get("external_id"))
                base = {
                    "id": row.get("id"), "external_id": row.get("external_id"),
                    "detail_id": raw_detail_id,
                    "source": source, "alert_seq": row.get("alert_seq"),
                    "relevance_score": row.get("relevance_score"),
                }
                if isinstance(extra, dict) and extra.get("_detail_text"):
                    receipt_rows.append(dict(base, status="already_detail"))
                    continue
                url = config.detail_fetch.url_template.replace("{id}", raw_detail_id)
                try:
                    response = http.get(
                        url, headers=config.headers,
                        timeout=httpx.Timeout(config.detail_fetch.timeout, connect=10.0),
                    )
                    response.raise_for_status()
                    recovered = detail_text(response.json(), config)
                    if not recovered:
                        raise ValueError("detail has no configured text")
                except Exception as exc:
                    receipt_rows.append(dict(base, status="fetch_failed", error=type(exc).__name__))
                    continue
                payload = update_payload(row, recovered, recovered_at)
                originals.append({key: row.get(key) for key in (
                    "id", "external_id", "source", "search_text", "extra_info",
                    "alert_seq", "relevance_score",
                )})
                receipt = dict(base, status="planned" if apply_changes else "would_update",
                               detail_length=len(recovered))
                receipt_rows.append(receipt)
                if apply_changes:
                    updates.append((row, payload, receipt))
                time.sleep(max(0.0, 1.0 / float(config.rate_limit or 1.0)))
    if apply_changes:
        # Backup all exact target fields before the first database mutation.
        _write(Path(backup_path), {"created_at": recovered_at, "rows": originals})
        for row, payload, receipt in updates:
            try:
                client.table("tenders").update(payload).eq("id", row["id"]).execute()
                receipt["status"] = "updated"
            except Exception as exc:
                receipt["status"] = "update_failed"
                receipt["error"] = type(exc).__name__
    counts = {}
    for row in receipt_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"generated_at": recovered_at, "applied": apply_changes,
            "gaps": [{"source": source, "low_exclusive": low, "high_inclusive": high}
                     for source, low, high in gaps],
            "exact_target_count": len(exact_targets or []),
            "counts": counts, "rows": receipt_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--gap", action="append", type=parse_gap)
    inputs.add_argument("--targets", help="JSON manifest with exact source/detail_id targets")
    parser.add_argument("--config", default="crawler/config/sources.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--backup")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--backup is required with --apply")
    exact_targets = load_targets(args.targets) if args.targets else None
    report = recover(args.gap or [], args.config, args.apply, args.backup, exact_targets)
    _write(Path(args.output), report)
    print(json.dumps({"output": args.output, "applied": args.apply,
                      "counts": report["counts"]}, ensure_ascii=False))
    return 0 if not (report["counts"].get("fetch_failed") or
                     report["counts"].get("update_failed") or
                     report["counts"].get("missing_row")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
