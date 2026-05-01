"""UZEX prequalification enricher — fetches /api/Public/GetLot?id=N for tenders missing details.

Public endpoint, no auth. Run after main crawl.

Usage:
    python3 uzex_prequal_enricher.py                # enrich all UZEX prequal tenders missing extra_info.lots
    python3 uzex_prequal_enricher.py --limit 100    # cap
    python3 uzex_prequal_enricher.py --tender-id <uuid>
"""
import argparse
import json
import os
import sys
import time
from typing import Dict, Any, Optional

import httpx
from dotenv import load_dotenv

sys.path.insert(0, "/opt/parsing-seo")
load_dotenv("/opt/parsing-seo/.env")

from crawler.core.db import _get_client  # type: ignore

API_HOST = "https://xarid-api-prequest.uzex.uz"
SOURCE = "UZEX Предквалификации"


def fetch_detail(external_id: str, client: httpx.Client) -> Optional[Dict[str, Any]]:
    # Strip id_prefix like "uzex-prq-" if it leaked into external_id
    raw = str(external_id).split("-")[-1] if "-" in str(external_id) else str(external_id)
    if not raw.isdigit():
        return None
    last_err = None
    for attempt in range(3):
        try:
            r = client.get(f"{API_HOST}/api/Public/GetLot", params={"id": raw}, timeout=30)
            if r.status_code != 200:
                return None
            body = r.json()
            if body.get("Status") != 200:
                return None
            return body.get("Data") or None
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ProxyError) as e:
            last_err = e
            time.sleep(2 + attempt * 3)
    print(f"FETCH ERR ext={raw}: {type(last_err).__name__}: {last_err}", flush=True)
    return None


def merge_into_extra(extra: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    extra = dict(extra or {})
    extra["lots"] = data.get("details") or []
    extra["files"] = data.get("files") or []
    extra["advance_cost"] = data.get("advanceCost")
    extra["advance_term_days"] = data.get("advanceTerm")
    extra["decree"] = data.get("decreeName")
    extra["delivery_address"] = data.get("deliveryAddress")
    extra["display_id"] = data.get("displayId")
    if data.get("customerInn"):
        extra["customer_inn"] = data["customerInn"]
    extra["enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return extra


def enrich_one(tid: str, sb, client: httpx.Client, force: bool = False) -> Dict[str, Any]:
    r = sb.table("tenders").select("id,external_id,source,extra_info").eq("id", tid).execute()
    if not r.data:
        return {"error": "not found"}
    row = r.data[0]
    if row["source"] != SOURCE:
        return {"error": f"wrong source: {row['source']}"}
    extra = row.get("extra_info") or {}
    if extra.get("lots") and not force:
        return {"skipped": "already enriched"}
    data = fetch_detail(row["external_id"], client)
    if not data:
        return {"error": "GetLot returned empty"}
    new_extra = merge_into_extra(extra, data)
    sb.table("tenders").update({"extra_info": new_extra}).eq("id", tid).execute()
    return {"updated": tid, "lots": len(new_extra.get("lots", [])), "files": len(new_extra.get("files", []))}


def batch(sb, client: httpx.Client, limit: int, force: bool):
    page_size = 200
    offset = 0
    enriched = 0
    failed = 0
    while enriched + failed < limit:
        q = sb.table("tenders").select("id,external_id,extra_info").eq("source", SOURCE).range(offset, offset + page_size - 1).execute()
        rows = q.data
        if not rows:
            break
        for row in rows:
            extra = row.get("extra_info") or {}
            if extra.get("lots") and not force:
                continue
            data = fetch_detail(row["external_id"], client)
            if not data:
                failed += 1
                print(f"FAIL {row['id']} ext={row['external_id']}")
                continue
            new_extra = merge_into_extra(extra, data)
            sb.table("tenders").update({"extra_info": new_extra}).eq("id", row["id"]).execute()
            enriched += 1
            print(f"OK   {row['id']} ext={row['external_id']} → {len(new_extra.get('lots',[]))} lots, {len(new_extra.get('files',[]))} files")
            time.sleep(0.5)  # 2 RPS
            if enriched + failed >= limit:
                break
        offset += page_size
    print(f"DONE enriched={enriched} failed={failed}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tender-id")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    sb = _get_client()
    proxy = os.environ.get("RESIDENTIAL_PROXY_URL")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    with httpx.Client(proxy=proxy, headers=headers, follow_redirects=True) as client:
        if args.tender_id:
            print(json.dumps(enrich_one(args.tender_id, sb, client, args.force), ensure_ascii=False, indent=2))
        else:
            batch(sb, client, args.limit, args.force)


if __name__ == "__main__":
    main()
