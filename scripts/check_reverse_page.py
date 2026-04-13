"""Check what's on ebirja reverse-auction page and intercept API calls."""
import asyncio
import re
from playwright.async_api import async_playwright


async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Intercept network requests
        api_requests = []

        async def on_request(req):
            url = req.url.lower()
            if any(k in url for k in ("api", "rpc", "reverse", "auction", "trade")):
                api_requests.append({"url": req.url, "method": req.method})

        page.on("request", on_request)

        print("Navigating to ebirja.uz/ru/trade/reverse-auction...")
        await page.goto("https://ebirja.uz/ru/trade/reverse-auction", timeout=30000)
        await asyncio.sleep(8)

        print("\n=== API REQUESTS INTERCEPTED ===")
        for r in api_requests:
            print("  %s %s" % (r["method"], r["url"]))

        print("\n=== TABLES ON PAGE ===")
        tables = await page.query_selector_all("table")
        print("  Found: %d tables" % len(tables))

        # Check for any data containers
        print("\n=== DATA CONTAINERS ===")
        for sel in ["table", ".table", "[class*=table]", "[class*=list]", "[class*=grid]", "[class*=card]", "article", ".tender", "[class*=tender]", "[class*=auction]", "[class*=trade]"]:
            els = await page.query_selector_all(sel)
            if els:
                print("  %s: %d elements" % (sel, len(els)))

        # Get page HTML and look for API URLs
        html = await page.content()
        print("\n=== API URLS IN HTML ===")
        patterns = re.findall(r'(https?://[^"\'\s<>]+(?:api|reverse|auction|trade)[^"\'\s<>]*)', html)
        for pat in set(patterns):
            print("  %s" % pat)

        # Check Next.js data
        next_data = await page.query_selector("script#__NEXT_DATA__")
        if next_data:
            text = await next_data.inner_text()
            print("\n=== __NEXT_DATA__ (first 1000) ===")
            print(text[:1000])

        await browser.close()


asyncio.run(check())
