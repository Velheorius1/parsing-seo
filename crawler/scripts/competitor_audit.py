#!/usr/bin/env python3
"""Read-only, cache-first audit of competitor procurement results.

Usage examples:

  python3 -m crawler.scripts.competitor_audit collect \
    --source deals --cache-dir /path/to/deals-pages --output-dir docs/audits/run
  python3 -m crawler.scripts.competitor_audit join --manifest docs/audits/run/deals-manifest.json
  python3 -m crawler.scripts.competitor_audit report --awards docs/audits/run/awards.jsonl

The tool never sends Telegram messages, reads .env, or writes production data.
Network collection is deliberately not implicit: use a separately reviewed public
adapter when a cache is absent. This keeps a report run reproducible and cheap.
"""
import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from crawler.core.competitor_audit import (
    award_in_window,
    collect_pages,
    load_registry,
    normalize_award,
    page_body,
    registry_inns,
)


ENDPOINTS = {
    "deals": "https://apietender.uzex.uz/api/common/DealsList",
    "direct": "https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases",
    "civil": "https://apietender.uzex.uz/api/CivilContracts/GetResulted",
}


def _numeric_path_order(path: Path) -> int:
    try:
        return int(path.name.split(".", 1)[0])
    except ValueError:
        return 10 ** 12


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(str(path), "rt", encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError("Cached page %s is %s, not list" % (path, type(rows).__name__))
    return rows


def _parsed_date(row: Dict[str, Any]) -> str:
    raw = row.get("deal_date") or row.get("contract_date") or row.get("date_ini") or ""
    text = str(raw).strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4:5] == "-":
        return text[:10]
    try:
        return datetime.strptime(text[:10], "%m/%d/%Y").date().isoformat()
    except ValueError:
        return ""


def _page_receipt(source: str, index: int, path: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    raw = path.read_bytes()
    dates = [value for value in (_parsed_date(row) for row in rows) if value]
    invalid_dates = sum(1 for row in rows
                        if (row.get("deal_date") or row.get("contract_date") or row.get("date_ini"))
                        and not _parsed_date(row))
    return {
        "page": index + 1,
        "file": path.name,
        "body": page_body(source, index),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(rows),
        "total": rows[0].get("total_count") if rows else None,
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "invalid_dates": invalid_dates,
    }


def collect_cache(source: str, cache_dir: Path, target_inns: Set[str]) -> Dict[str, Any]:
    """Reads saved pages once and returns an inspectable collection manifest."""
    if source not in ENDPOINTS:
        raise ValueError("Unsupported source: %s" % source)
    paths = sorted((path for path in cache_dir.iterdir() if path.is_file() and
                    path.name.split(".", 1)[0].isdigit()), key=_numeric_path_order)
    if not paths:
        raise ValueError("No numeric JSON cache pages in %s" % cache_dir)
    loaded = []
    for path in paths:
        loaded.append((path, _read_rows(path)))

    result = collect_pages(lambda index: loaded[index][1], source, len(loaded), target_inns)
    receipts = [_page_receipt(source, index, path, rows)
                for index, (path, rows) in enumerate(loaded[:result["page_count"]])]
    result.update({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "cache_read_only",
        "endpoint": ENDPOINTS[source],
        "raw_cache": str(cache_dir),
        "receipts": receipts,
    })
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def command_collect(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    manifest = collect_cache(args.source, Path(args.cache_dir), set(registry_inns(registry)))
    target = Path(args.output_dir) / (args.source + "-manifest.json")
    _write_json(target, manifest)
    print(json.dumps({
        "manifest": str(target), "source": args.source, "completion": manifest["completion"],
        "complete": manifest["complete"], "unique_rows": manifest["unique_row_count"],
        "unique_business_ids": manifest["unique_business_id_count"],
        "matches": len(manifest["matches"]), "error": manifest["error"],
    }, ensure_ascii=False))
    return 0 if manifest["complete"] else 2


def command_join(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    source = manifest.get("source")
    if source not in ENDPOINTS:
        raise ValueError("Manifest has unsupported source")
    awards = [normalize_award(source, row) for row in manifest.get("matches", [])]
    if args.date_from and args.date_to:
        awards = [award for award in awards
                  if award_in_window(award, args.date_from, args.date_to) is True]
    target = Path(args.output or Path(args.manifest).with_name(source + "-awards.jsonl"))
    count = _write_jsonl(target, awards)
    print(json.dumps({"awards": str(target), "count": count, "source": source}, ensure_ascii=False))
    return 0


def command_report(args: argparse.Namespace) -> int:
    awards = []
    with Path(args.awards).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                awards.append(json.loads(line))
    target = [award for award in awards if award.get("above_threshold") is True]
    summary = {
        "awards_total": len(awards),
        "above_threshold_total": len(target),
        "accepted_or_contract_published": sum(1 for award in target if award.get("is_win")),
        "open_competitions": sum(1 for award in target if award.get("is_open_competition") is True),
        "direct_contracts": sum(1 for award in target if award.get("is_open_competition") is False),
        "unknown_status_or_amount": sum(1 for award in awards
                                          if award.get("status") == "unknown" or award.get("above_threshold") is None),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="Создать manifest из сохранённого cache")
    collect.add_argument("--source", choices=sorted(ENDPOINTS), required=True)
    collect.add_argument("--cache-dir", required=True)
    collect.add_argument("--output-dir", required=True)
    collect.add_argument("--registry", default=None)
    collect.set_defaults(func=command_collect)
    join = subparsers.add_parser("join", help="Нормализовать совпадения manifest в awards JSONL")
    join.add_argument("--manifest", required=True)
    join.add_argument("--output")
    join.add_argument("--date-from", help="YYYY-MM-DD, граница по awarded_at")
    join.add_argument("--date-to", help="YYYY-MM-DD, граница по awarded_at")
    join.set_defaults(func=command_join)
    report = subparsers.add_parser("report", help="Сводка нормализованных awards")
    report.add_argument("--awards", required=True)
    report.set_defaults(func=command_report)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
