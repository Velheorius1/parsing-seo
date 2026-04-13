"""Investigate disabled sources to find APIs and access methods."""
import httpx
import asyncio
import re

PROXY = "http://5YvlI8GEpKeSbSVH:3S2mMuQU3CKZ0lEw_country-uz@geo.iproyal.com:12321"


async def investigate():
    # 1. TenderZone — search for API
    print("=== TENDERZONE ===")
    urls = [
        "https://trade.tzone.uz/api/tenders",
        "https://trade.tzone.uz/api/lots",
        "https://api.tzone.uz/tenders",
        "https://trade.tzone.uz/api/v1/lots",
        "https://trade.tzone.uz/api/v1/tenders",
    ]
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
                r = await c.get(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
                print("  GET %s -> %d (%d bytes)" % (url, r.status_code, len(r.text)))
                if r.status_code == 200 and len(r.text) > 10:
                    print("    Body: %s" % r.text[:300])
        except Exception as e:
            print("  GET %s -> ERR: %s" % (url, str(e)[:50]))

    # 2. dxarid/exarid — check URLs
    print("\n=== DXARID/EXARID ===")
    pairs = [
        ("dxarid old", "https://apidxarid.uzex.uz/api/Common/TradeList"),
        ("dxarid new", "https://api-dxarid.uzex.uz/api/Common/TradeList"),
        ("exarid old", "https://apiexarid.uzex.uz/api/Common/TradeList"),
        ("exarid new", "https://api-exarid.uzex.uz/api/Common/TradeList"),
        ("dxarid xarid", "https://xarid-api-dxarid.uzex.uz/api/Common/TradeList"),
    ]
    for name, url in pairs:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.post(url, json={"from": 0, "to": 3}, headers={"Content-Type": "application/json"})
                print("  %s -> %d (%d bytes)" % (name, r.status_code, len(r.text)))
                if r.status_code == 200:
                    print("    Body: %s" % r.text[:200])
        except Exception as e:
            print("  %s -> ERR: %s" % (name, str(e)[:50]))

    # Also try with proxy
    print("\n=== DXARID/EXARID via PROXY ===")
    for name, url in pairs[:2]:
        try:
            async with httpx.AsyncClient(proxy=PROXY, timeout=12) as c:
                r = await c.post(url, json={"from": 0, "to": 3}, headers={"Content-Type": "application/json"})
                print("  %s (proxy) -> %d (%d bytes)" % (name, r.status_code, len(r.text)))
                if r.status_code == 200:
                    print("    Body: %s" % r.text[:200])
        except Exception as e:
            print("  %s (proxy) -> ERR: %s" % (name, str(e)[:50]))

    # 3. agro.uzex.uz
    print("\n=== AGRO.UZEX ===")
    try:
        async with httpx.AsyncClient(proxy=PROXY, timeout=10, follow_redirects=True) as c:
            r = await c.get("https://agro.uzex.uz", headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
            print("  Status: %d, Length: %d" % (r.status_code, len(r.text)))
            if "__next" in r.text or "react" in r.text.lower():
                print("  SPA detected")
            api_urls = re.findall(r'(https?://[^"\s<>]+api[^"\s<>]*)', r.text)
            print("  API URLs in HTML: %s" % list(set(api_urls))[:5])
            # Check for data in HTML
            titles = re.findall(r'<h[234][^>]*>([^<]{5,})</h', r.text)
            print("  Headings: %s" % titles[:5])
    except Exception as e:
        print("  ERR: %s" % str(e)[:80])


asyncio.run(investigate())
