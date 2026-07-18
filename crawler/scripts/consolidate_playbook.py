"""consolidate_playbook — one-time cleanup of pre-E-F legacy candidates (A3, 2026-07-16).

Before the E-F controlled-vocab fix (ebded36), corrections got free-form signal_slugs,
so 186 candidates piled up at support_count=1 that can NEVER promote (new corrections
snap to the 13 controlled slugs). They don't touch the prompt (only ACTIVE feeds it) but
bury the real signal and make the candidate count meaningless.

This groups candidates by a normalized (taxonomy:controlled-slug) key and:
  1. RETIRE-REDUNDANT (safe, default --execute): a candidate whose group maps to an
     already-ACTIVE key is covered by a live principle → status='retired'. Pure declutter,
     zero prompt change. (~137 rows in the 2026-07-16 snapshot.)
  2. MERGE-PROMOTE (opt-in --promote): candidate groups of >=2 that form a NEW key → one
     canonical row promoted to active with summed support, the rest retired. (~9 groups.)
     relevant-rejected (recall-side) merges are NEVER auto-promoted — they affect recall and
     are printed for MANUAL review (a bad recall guard silently widens the net).

SAFE by design (mirrors migrate_legacy_urls.py): dry-run default, backs up EVERY row to
logs/ before any write, retire never deletes. Prod-DML → Tier-3: Daniyar runs --execute.

Usage:
  python3 -m crawler.scripts.consolidate_playbook              # dry-run: report + backup
  python3 -m crawler.scripts.consolidate_playbook --execute    # apply retire-redundant only
  python3 -m crawler.scripts.consolidate_playbook --execute --promote  # + merge-promote new groups
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/parsing-seo")

# Controlled slug anchors (must stay in sync with playbook_refine.SIGNAL_SLUGS).
# Order matters — first regex match wins.
RULES = [
    ("construction", r"stroit|construct|obshchestroit|\bmaterial|syrie|syrjov|prokat|trub[iy]|zhbi|beton|metall|infrastruk|armatur|list-bez|sortovoy"),
    ("greeting-offtopic", r"greeting|privet|slogan|lozung|emodzi|emoji|gosudarstv|simvolik|state-symbol|cennost|foreign-greet|hashtag|tag-only|wedding|prazdn|pozdrav"),
    ("vacancy", r"vacan|vakans|hiring|personal|ispolnitel|master|\busta\b|poisk-rabot|skill-based|kadr|trebuetsya|poisk-kontakt|poisk-lic|zapis-v-shkol|perepis"),
    ("self-promo", r"promo|reklam|self-promo|\bbrand|advert|commercial-offer|own-product|own-stock|\boffer|premium|kachestv|sifat|nalichie|otpish|srochno-cena|prodazh|otdast|creativ"),
    ("textile-sewing", r"textile|sewing|poshiv|garment|bodik|shvej"),
    ("non-print-goods", r"non-print|goods|tovar|\bproduct|oborudovan|split-sistem|zapchast|mebel|bytov|konsumabl|consumable|\bink|cmyk|nomenclatur|specialized-consum|remont|boiler|kotl|split"),
    ("services-nonprint", r"servic|uslug|event|meropriyat|soveshchan|otraslev|it-|smm|izdatelsk|publik|nauchn|ekspertiz|prombez|promyshlennaya-bez|regulyator|razvit|program|opyt"),
    ("medicine-food", r"medicine|food|produkt-|lekarstv|ingredient"),
    ("cutting-outdoor", r"cutting|naruzhk|signage|vitraj|shtamp"),
    ("print-rejected", r"print-reject|poligraf|pechat|tirazh|edinichn|personaliz|shtuchn"),
    ("packaging-rejected", r"packaging-reject|upakov|paket"),
    ("score-borderline", r"borderline|informaln|informal|neformaln|vague|chastnyy-zakaz|direct-client|slang"),
]
_CONTROLLED = set(s for s, _ in RULES) | {"other"}


def _norm_slug(slug):
    s = (slug or "").lower()
    for canon, pat in RULES:
        if re.search(pat, s):
            return canon
    return "other"


def _client():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _fetch_all(client):
    rows = []
    off = 0
    while True:
        d = (client.table("classifier_playbook")
             .select("id,signal_key,taxonomy,status,support_count,principle,example")
             .range(off, off + 999).execute().data) or []
        rows.extend(d)
        if len(d) < 1000:
            break
        off += 1000
    return rows


def _canonical(members):
    """Pick the row to keep in a merge group: prefer a clean controlled slug, else longest principle."""
    clean = [m for m in members if m["signal_key"].partition(":")[2] in _CONTROLLED]
    pool = clean or members
    return max(pool, key=lambda m: len((m.get("principle") or "")))


def main(execute, promote):
    client = _client()
    rows = _fetch_all(client)
    active_keys = set(r["signal_key"] for r in rows if r["status"] == "active")
    cands = [r for r in rows if r["status"] == "candidate"]
    print("playbook rows: %d (active=%d candidate=%d)" % (len(rows), len(active_keys), len(cands)))

    # BACKUP everything first (always, even dry-run) — mirrors migrate_legacy_urls.py.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bkp = "/opt/parsing-seo/logs/playbook_consolidate_backup_%s.jsonl" % ts
    try:
        os.makedirs(os.path.dirname(bkp), exist_ok=True)
        with open(bkp, "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("backup -> %s (%d rows)" % (bkp, len(rows)))
    except IOError as e:
        print("BACKUP FAILED: %s — aborting" % e)
        return 1

    groups = {}
    for r in cands:
        tax, _, slug = r["signal_key"].partition(":")
        nk = "%s:%s" % (tax, _norm_slug(slug))
        groups.setdefault(nk, []).append(r)

    retire_redundant = []   # candidate ids covered by an active principle
    merges = []             # (nk, canonical, others, recall?)
    for nk, members in groups.items():
        if nk in active_keys:
            retire_redundant.extend(members)
        elif nk.endswith(":other") or len(members) < 2:
            continue  # leave uncategorized / genuine one-offs untouched
        else:
            canon = _canonical(members)
            others = [m for m in members if m["id"] != canon["id"]]
            is_recall = nk.startswith("relevant-rejected")
            merges.append((nk, canon, others, is_recall))

    total_support = lambda c, o: (c.get("support_count") or 1) + sum((m.get("support_count") or 1) for m in o)

    print("\n=== 1) RETIRE-REDUNDANT (covered by an active principle): %d candidates ===" % len(retire_redundant))
    print("=== 2) MERGE-PROMOTE groups (>=2, new key): %d ===" % len(merges))
    for nk, canon, others, is_recall in sorted(merges, key=lambda x: -total_support(x[1], x[2])):
        tag = "  [RECALL → MANUAL, not auto-promoted]" if is_recall else ""
        print("\n  %-42s support→%d%s" % (nk, total_support(canon, others), tag))
        print("     keep: %s" % (canon.get("principle") or "")[:100])
        for m in others:
            print("     merge<-%s" % m["signal_key"])

    if not execute:
        print("\n>>> DRY-RUN — nothing written. --execute = retire-redundant; --execute --promote = also merge.")
        return 0

    # APPLY 1: retire redundant
    done_ret = 0
    for m in retire_redundant:
        try:
            client.table("classifier_playbook").update({"status": "retired"}).eq("id", m["id"]).execute()
            done_ret += 1
        except Exception as e:
            print("  retire failed id=%s: %s" % (m["id"], str(e)[:60]))
    print("\n>>> RETIRED %d redundant candidates" % done_ret)

    # APPLY 2: merge-promote (opt-in, non-recall only)
    if promote:
        done_m = 0
        for nk, canon, others, is_recall in merges:
            if is_recall:
                print("  skip recall group %s (manual review)" % nk)
                continue
            try:
                client.table("classifier_playbook").update(
                    {"status": "active", "support_count": total_support(canon, others)}
                ).eq("id", canon["id"]).execute()
                for m in others:
                    client.table("classifier_playbook").update({"status": "retired"}).eq("id", m["id"]).execute()
                done_m += 1
            except Exception as e:
                print("  merge failed %s: %s" % (nk, str(e)[:60]))
        print(">>> PROMOTED %d merged groups (recall groups left for manual review)" % done_m)
    else:
        print(">>> merge-promote skipped (add --promote to apply the %d groups)" % len(merges))

    # verify
    after = _fetch_all(client)
    by = {}
    for r in after:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(">>> AFTER:", by, "| backup:", bkp)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply retire-redundant")
    ap.add_argument("--promote", action="store_true", help="also merge-promote new >=2 groups (non-recall)")
    a = ap.parse_args()
    sys.exit(main(a.execute, a.promote))
