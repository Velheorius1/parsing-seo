#!/usr/bin/env python3
"""Cache public Ebirja contract cards without any production side effects.

Unlike ``fetch_ebirja_contracts``, this tool does not upsert to Supabase and
does not send Telegram alerts. It deliberately makes no claim of completeness:
the manifest records requested page caps per procurement type so a later run
can resume or reconcile the archive before it is used for annual winner stats.
"""
import argparse
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from crawler.scripts.fetch_ebirja_contracts import (
    CONTRACT_TYPES,
    _click_next_page,
    _extract_cards_from_page,
    _parse_contract_text,
)


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _contract_date(row: Dict[str, Any]) -> date:
    return datetime.strptime(str(row.get("deadline") or ""), "%d.%m.%Y").date()


def _is_complete(source_runs: List[Dict[str, Any]]) -> bool:
    """Only an observed date boundary or an empty first/last page proves end.

    A missing pagination control is a UI/parser failure until proven otherwise;
    it must never silently certify an annual archive as complete.
    """
    return all(source.get("completion") in ("date_boundary", "empty_page")
               for source in source_runs)


async def _collect_type(page: Any, contract_type: str, page_cap: int, date_from: date) -> Dict[str, Any]:
    """Walk one public type until its newest page is older than the audit window."""
    await page.goto(CONTRACT_TYPES[contract_type]["url"], wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(5000)
    rows = []
    pages = 0
    completion = "page_cap"
    while pages < page_cap:
        cards = await _extract_cards_from_page(page)
        page_rows = []
        for card in cards:
            parsed = _parse_contract_text(card.get("text", ""), card.get("link", ""), contract_type)
            if parsed:
                page_rows.append(parsed)
        if not page_rows:
            completion = "empty_page"
            break
        rows.extend(page_rows)
        pages += 1
        # Registry is newest-first. Only stop once every valid date on this page
        # predates the requested window; missing/unparseable dates fail open.
        dates = []
        for row in page_rows:
            try:
                dates.append(_contract_date(row))
            except ValueError:
                pass
        if dates and len(dates) == len(page_rows) and max(dates) < date_from:
            completion = "date_boundary"
            break
        if pages >= page_cap:
            break
        if not await _click_next_page(page):
            completion = "no_next_page"
            break
    return {
        "type": contract_type,
        "source_url": CONTRACT_TYPES[contract_type]["url"],
        "requested_pages": page_cap,
        "pages_collected": pages,
        "completion": completion,
        "rows": rows,
    }


async def collect(types: List[str], pages: int, date_from: date) -> Dict[str, Any]:
    """Collect public cards; caller owns output persistence and scheduling."""
    from playwright.async_api import async_playwright

    source_runs = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for contract_type in types:
                page = await browser.new_page()
                try:
                    source_runs.append(await _collect_type(page, contract_type, pages, date_from))
                finally:
                    await page.close()
        finally:
            await browser.close()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "public_ui_read_only",
        "date_from": date_from.isoformat(),
        "complete": _is_complete(source_runs),
        "completion": "date_boundary_or_end" if _is_complete(source_runs) else "incomplete",
        "sources": source_runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", choices=list(CONTRACT_TYPES) + ["all"], default="all")
    parser.add_argument("--pages", type=int, default=1, help="Positive per-type page cap")
    parser.add_argument("--date-from", default=None, help="Inclusive YYYY-MM-DD cutoff; default is 365 days ago")
    parser.add_argument("--output", required=True, help="Path for the inspectable JSON manifest")
    args = parser.parse_args()
    if args.pages < 1:
        parser.error("--pages must be positive")
    try:
        date_from = date.fromisoformat(args.date_from) if args.date_from else date.today().replace(year=date.today().year - 1)
    except ValueError:
        parser.error("--date-from must be YYYY-MM-DD")
    types = list(CONTRACT_TYPES) if args.type == "all" else [args.type]
    result = asyncio.run(collect(types, args.pages, date_from))
    target = Path(args.output)
    _write_json(target, result)
    print(json.dumps({
        "manifest": str(target),
        "types": types,
        "rows": sum(len(source["rows"]) for source in result["sources"]),
        "complete": result["complete"],
        "completion": result["completion"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
