"""One-time migration: rewrite dead legacy source_url patterns (E-B, 2026-07-06).

Old DB rows (collected before the 2026-06-21 URL-template fix) carry pre-migration
UZEX URLs that redirect to the homepage — they live in past Telegram alerts and
Vercel cards. This rewrites them to the browser-verified new-xarid routes.

SAFE by design: additive correction (dead link → working link, no data loss);
backs up every (id, source, old_url) to logs/ before touching anything; dry-run
default; scoped to ALERTED rows (what Daniyar actually sees) unless --all.

Usage:
  python3 scripts/migrate_legacy_urls.py            # dry-run: count + sample + backup
  python3 scripts/migrate_legacy_urls.py --execute  # apply the rewrite
  python3 scripts/migrate_legacy_urls.py --execute --all   # incl. non-alerted (dormant)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/parsing-seo")
from crawler.config.settings import settings

# Same map as recall_audit._LEGACY_URL_FIXES (browser-verified 2026-06-21 routes).
FIXES = [
    (re.compile(r"https://xarid\.uzex\.uz/prequalification/detail/(\d+)"),
     r"https://new-xarid.uzex.uz/home/purchase/proposal-request/detail/\1"),
    (re.compile(r"https://xarid\.uzex\.uz/shop/lot-details/(\d+)"),
     r"https://new-xarid.uzex.uz/home/shop/detail/\1?elektron=true"),
]
LIKE_PATTERNS = ["%xarid.uzex.uz/prequalification/detail/%", "%xarid.uzex.uz/shop/lot-details/%"]


def _rewrite(url):
    for rx, repl in FIXES:
        new = rx.sub(repl, url)
        if new != url:
            return new
    return None


def main(execute, all_rows):
    from supabase import create_client
    c = create_client(settings.supabase_url, settings.supabase_service_role_key)
    rows = []
    for pat in LIKE_PATTERNS:
        off = 0
        while True:
            q = c.table("tenders").select("id,source,source_url,alert_seq").like("source_url", pat)
            if not all_rows:
                q = q.not_.is_("alert_seq", "null")
            d = (q.range(off, off + 999).execute().data) or []
            rows.extend(d)
            if len(d) < 1000:
                break
            off += 1000
    # de-dup by id
    seen = {}
    for r in rows:
        seen[r["id"]] = r
    rows = list(seen.values())
    changed = [(r, _rewrite(r["source_url"])) for r in rows]
    changed = [(r, nu) for r, nu in changed if nu]
    scope = "ALL (incl non-alerted)" if all_rows else "alerted only"
    print("legacy-URL rows (%s): %d" % (scope, len(changed)))
    for r, nu in changed[:6]:
        print("  %s\n    -> %s" % (r["source_url"][:60], nu[:60]))

    # BACKUP (always, even dry-run)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bkp = "/opt/parsing-seo/logs/legacy_url_backup_%s.jsonl" % ts
    try:
        os.makedirs(os.path.dirname(bkp), exist_ok=True)
        with open(bkp, "w") as f:
            for r, nu in changed:
                f.write(json.dumps({"id": r["id"], "source": r["source"],
                                    "old": r["source_url"], "new": nu}, ensure_ascii=False) + "\n")
        print("backup -> %s (%d rows)" % (bkp, len(changed)))
    except IOError as e:
        print("BACKUP FAILED: %s — aborting" % e)
        return 1

    if not execute:
        print(">>> DRY-RUN — nothing written. Re-run with --execute to apply.")
        return 0
    done = 0
    for r, nu in changed:
        try:
            c.table("tenders").update({"source_url": nu}).eq("id", r["id"]).execute()
            done += 1
        except Exception as e:
            print("  update failed id=%s: %s" % (r["id"], str(e)[:60]))
    print(">>> EXECUTED: %d rewritten. Backup at %s" % (done, bkp))
    # verify none remain
    left = 0
    for pat in LIKE_PATTERNS:
        q = c.table("tenders").select("id", count="exact").like("source_url", pat)
        if not all_rows:
            q = q.not_.is_("alert_seq", "null")
        left += q.execute().count or 0
    print(">>> VERIFY: %d legacy URLs remain in scope (expect 0)" % left)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--all", action="store_true", help="incl. non-alerted dormant rows")
    a = ap.parse_args()
    sys.exit(main(a.execute, a.all))
