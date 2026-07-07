"""Backfill offer-enrichment for past Cooperation.uz lot alerts (E-D, 2026-07-06).

The offer-join (price × qty, reference supplier, photo) was fixed forward on
2026-07-05 (commit 701f692) but past alerted lots stayed bare (price NULL). This
re-runs the SAME join over already-alerted Cooperation.uz Лоты that lack a price,
persisting sum/supplier/photo so their Vercel cards become workable retroactively.

Reuses fetch_cooperation._fetch_offer_detail. cooperation.uz blocks datacenter IPs,
so proxy env is set from settings before the join (trust_env picks it up).

Usage: python3 scripts/backfill_coop_enrichment.py [--limit N] [--execute]
Default DRY-RUN (shows what would change, no writes).
"""

import argparse
import os
import sys

sys.path.insert(0, "/opt/parsing-seo")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawler.config.settings import settings

# cooperation blocks datacenter IPs → route the offer-join through the UZ proxy
# (httpx.Client trust_env picks these up).
_px = settings.residential_proxy_url
if _px:
    os.environ["HTTP_PROXY"] = _px
    os.environ["HTTPS_PROXY"] = _px

from supabase import create_client
import fetch_cooperation as fc  # noqa: E402  (after proxy env is set)


def main(limit, execute):
    c = create_client(settings.supabase_url, settings.supabase_service_role_key)
    # alerted coop lots with no price and an offer number to join on
    rows = (c.table("tenders")
            .select("external_id,source,title,price,extra_info")
            .eq("source", "Cooperation.uz Лоты").not_.is_("alert_seq", "null")
            .is_("price", "null").limit(limit).execute().data) or []
    todo = [r for r in rows if (r.get("extra_info") or {}).get("offer")]
    print("alerted coop lots without price: %d | with offer to join: %d (dry_run=%s)"
          % (len(rows), len(todo), not execute))
    enriched = 0
    for r in todo:
        ei = dict(r.get("extra_info") or {})
        od = fc._fetch_offer_detail(r.get("title", ""), ei["offer"])
        if not od:
            continue
        ei.update({k: v for k, v in od.items() if v is not None})
        price = None
        try:
            if ei.get("unit_price") and ei.get("quantity"):
                price = float(ei["unit_price"]) * float(ei["quantity"])
        except (TypeError, ValueError):
            price = None
        tag = "%s | %s | %s" % (
            ("{:,.0f}".format(price) if price else "no-price"),
            ei.get("ref_supplier", "no-supplier"),
            "photo" if ei.get("photo") else "no-photo")
        print("  %s → %s" % ((r.get("title") or "")[:34], tag))
        if execute:
            c.table("tenders").update({"extra_info": ei, "price": price}) \
                .eq("external_id", r["external_id"]).eq("source", r["source"]).execute()
            enriched += 1
    print(">>> %s: %d lots enriched" % ("EXECUTED" if execute else "DRY-RUN", enriched))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    sys.exit(main(a.limit, a.execute))
