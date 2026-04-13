"""Investigate SPA sources via Playwright — intercept API calls."""
import asyncio
from playwright.async_api import async_playwright


async def intercept_page(url, name, wait_time=12):
    """Open page, wait for JS load, intercept API requests."""
    print("\n=== %s ===" % name)
    print("URL: %s" % url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        api_responses = []

        async def on_response(resp):
            u = resp.url
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "api" in u.lower():
                try:
                    body = await resp.text()
                    api_responses.append({
                        "url": u,
                        "status": resp.status,
                        "size": len(body),
                        "body": body[:500],
                    })
                except Exception:
                    api_responses.append({"url": u, "status": resp.status, "size": 0, "body": ""})

        page.on("response", on_response)

        try:
            await page.goto(url, timeout=20000)
        except Exception as e:
            print("  Navigation error: %s" % str(e)[:80])

        await asyncio.sleep(wait_time)

        # Print API calls
        print("  API responses intercepted: %d" % len(api_responses))
        for r in api_responses:
            print("    %d %s (%d bytes)" % (r["status"], r["url"][:100], r["size"]))
            if r["size"] > 0 and r["size"] < 2000 and "json" in str(r.get("body", "")):
                print("      Body: %s" % r["body"][:300])

        # Check visible content
        body_text = await page.inner_text("body")
        lines = [l.strip() for l in body_text.split("\n") if l.strip()]
        print("  Visible text lines: %d" % len(lines))
        for line in lines[:10]:
            print("    %s" % line[:100])

        # Check tables/lists
        for sel in ["table", "tr", ".card", "article", "[class*=tender]", "[class*=lot]", "[class*=item]"]:
            els = await page.query_selector_all(sel)
            if els:
                print("  Selector '%s': %d elements" % (sel, len(els)))

        await browser.close()


async def main():
    # TenderZone
    await intercept_page("https://trade.tzone.uz", "TenderZone", 15)

    # agro.uzex.uz
    await intercept_page("https://agro.uzex.uz", "Agro UZEX", 10)

    # Beeline
    await intercept_page("https://beeline.uz/ru/about/tenderi", "Beeline UZ", 10)

    # Uzsuv (Nuxt.js)
    await intercept_page("https://uzsuv.uz/ru/tenderss", "Uzsuv", 10)

    # Bnect
    await intercept_page("https://uz.bnect.pro/procurement", "Bnect", 10)

    # MinZdrav
    await intercept_page("https://ssv.uz/ru/ssv/sections/tenderlar", "MinZdrav", 10)


asyncio.run(main())
