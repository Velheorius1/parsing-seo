#!/usr/bin/env python3
"""Recover specification text for bounded source/ID ranges after a cursor gap.

Dry-run is the default.  ``--apply`` updates only search_text and extra_info for
rows found in the requested ranges and requires an explicit backup path.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import httpx

from crawler.adapters.api import _resolve_wildcard_path, _safe_str
from crawler.core.db import _get_client, _merge_detail_text
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


def fetch_rows(client, source, ids):
    # type: (Any, str, List[str]) -> List[Dict[str, Any]]
    rows = []
    for start in range(0, len(ids), 100):
        result = (client.table("tenders").select(FIELDS).eq("source", source)
                  .in_("external_id", ids[start:start + 100]).execute())
        rows.extend(result.data or [])
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


def recover(gaps, config_path, apply_changes=False, backup_path=None):
    # type: (Iterable[Tuple[str, int, int]], str, bool, Any) -> Dict[str, Any]
    if apply_changes and backup_path is None:
        raise ValueError("--backup is required with --apply")
    configs = {source.name: source for source in load_sources(config_path) if source.detail_fetch}
    client = _get_client()
    recovered_at = datetime.now(timezone.utc).isoformat()
    receipt_rows, originals, updates = [], [], []
    with httpx.Client(follow_redirects=True) as http:
        for source, low, high in gaps:
            config = configs.get(source)
            if config is None:
                raise ValueError("source has no detail_fetch config: %s" % source)
            rows = fetch_rows(client, source, candidate_ids(low, high))
            for row in rows:
                extra = row.get("extra_info") or {}
                base = {
                    "id": row.get("id"), "external_id": row.get("external_id"),
                    "source": source, "alert_seq": row.get("alert_seq"),
                    "relevance_score": row.get("relevance_score"),
                }
                if isinstance(extra, dict) and extra.get("_detail_text"):
                    receipt_rows.append(dict(base, status="already_detail"))
                    continue
                url = config.detail_fetch.url_template.replace("{id}", str(row["external_id"]))
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
            "counts": counts, "rows": receipt_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", action="append", type=parse_gap, required=True)
    parser.add_argument("--config", default="crawler/config/sources.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--backup")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--backup is required with --apply")
    report = recover(args.gap, args.config, args.apply, args.backup)
    _write(Path(args.output), report)
    print(json.dumps({"output": args.output, "applied": args.apply,
                      "counts": report["counts"]}, ensure_ascii=False))
    return 0 if not (report["counts"].get("fetch_failed") or
                     report["counts"].get("update_failed")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
