"""orginfo.uz enrichment by INN — extracts contacts (phone/email/address/CEO) for tender customers.

Uses Playwright (headless) to render pages — orginfo data lives in JS-rendered text.

Usage:
    python3 orginfo_enricher.py --inn 306303488         # one-shot lookup
    python3 orginfo_enricher.py --tender-id <uuid>      # enrich one DB row (uses customer_inn from extra_info)
    python3 orginfo_enricher.py --batch                 # backfill: find tenders missing customer_contacts
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from playwright.async_api import async_playwright

sys.path.insert(0, "/opt/parsing-seo")
load_dotenv("/opt/parsing-seo/.env")

from crawler.core.db import _get_client  # type: ignore

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
SEARCH_URL = "https://orginfo.uz/ru/search/all/?q={q}"
ORG_RE = re.compile(r"/ru/organization/([0-9a-f]+)/")


async def _lookup_async(inn: str) -> Optional[Dict[str, Any]]:
    if not inn or not str(inn).strip().isdigit():
        return None
    inn = str(inn).strip()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="ru-RU", user_agent=UA)
        page = await ctx.new_page()
        try:
            await page.goto(SEARCH_URL.format(q=inn), wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            html = await page.content()
            m = ORG_RE.search(html)
            if not m:
                return None
            slug = m.group(1)
            org_url = f"https://orginfo.uz/ru/organization/{slug}/"
            await page.goto(org_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            text = await page.evaluate("() => document.body.innerText")
        finally:
            await browser.close()

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    def find_after(label_keywords, validate=None, max_skip=5):
        for i, ln in enumerate(lines):
            for kw in label_keywords:
                if ln == kw or ln.lower() == kw.lower():
                    for j in range(i + 1, min(i + max_skip, len(lines))):
                        cand = lines[j]
                        if validate and not validate(cand):
                            continue
                        if cand and cand not in label_keywords and len(cand) < 300:
                            return cand
        return None

    email_m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    email = email_m.group(0) if email_m else None

    phone = find_after(
        ["Номер телефона", "Телефон"],
        validate=lambda s: bool(re.match(r"^[\d\+\(\)\s\-]{7,25}$", s)),
    )
    if phone:
        phone = re.sub(r"\s+", "", phone)

    addr = find_after(
        ["Адрес"],
        validate=lambda s: any(c.isalpha() for c in s) and len(s) > 10 and "ссылка" not in s.lower(),
    )

    name = find_after(["Официальное название организации"]) or find_after(["Краткое название организации"])

    ceo = find_after(
        ["Руководитель", "Директор"],
        validate=lambda s: bool(re.match(r"^[A-ZА-ЯЁ][\w`’\s]+", s)) and len(s) > 5,
    )

    return {
        "inn": inn,
        "name": name,
        "phone": phone,
        "email": email,
        "address": addr,
        "ceo": ceo,
        "link": org_url,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def lookup_by_inn(inn: str) -> Optional[Dict[str, Any]]:
    return asyncio.run(_lookup_async(inn))


def enrich_tender(tid: str, sb, force: bool = False) -> Dict[str, Any]:
    r = sb.table("tenders").select("id,external_id,source,extra_info,organization").eq("id", tid).execute()
    if not r.data:
        return {"error": "not found"}
    row = r.data[0]
    extra = row.get("extra_info") or {}
    if extra.get("customer_contacts") and not force:
        return {"skipped": "already enriched", "contacts": extra["customer_contacts"]}
    inn = extra.get("customer_inn")
    if not inn:
        return {"error": "no customer_inn in extra_info", "tender_id": tid}
    contacts = lookup_by_inn(str(inn))
    if not contacts:
        return {"error": "orginfo lookup empty", "inn": inn}
    extra["customer_contacts"] = contacts
    sb.table("tenders").update({"extra_info": extra}).eq("id", tid).execute()
    return {"updated": tid, "contacts": contacts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inn")
    ap.add_argument("--tender-id")
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    sb = _get_client()
    if args.inn:
        print(json.dumps(lookup_by_inn(args.inn), ensure_ascii=False, indent=2))
        return
    if args.tender_id:
        print(json.dumps(enrich_tender(args.tender_id, sb, args.force), ensure_ascii=False, indent=2))
        return
    if args.batch:
        r = sb.table("tenders").select("id,extra_info").not_.is_("extra_info", "null").limit(args.limit * 5).execute()
        done = 0
        for row in r.data:
            extra = row.get("extra_info") or {}
            if not extra.get("customer_inn"):
                continue
            if extra.get("customer_contacts") and not args.force:
                continue
            res = enrich_tender(row["id"], sb, args.force)
            print(f'{row["id"]}: {json.dumps(res.get("contacts", res), ensure_ascii=False)}')
            done += 1
            if done >= args.limit:
                break
            time.sleep(1.0)
        print(f"DONE enriched {done}")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
