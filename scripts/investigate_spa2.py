"""Investigate SPA sources via Playwright — intercept API calls."""
import asyncio
from playwright.async_api import async_playwright


async def intercept_page(url, name, wait_time=10):
    print("\n=== %s ===" % name)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        api_responses = []

        async def on_response(resp):
            u = resp.url
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "/api/" in u:
                try:
                    body = await resp.text()
                    api_responses.append({"url": u, "status": resp.status, "size": len(body), "body": body[:500]})
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        except Exception as e:
            print("  Nav error: %s" % str(e)[:60])

        await asyncio.sleep(wait_time)

        print("  API responses: %d" % len(api_responses))
        for r in api_responses[:10]:
            print("    %d %s (%d)" % (r["status"], r["url"][:90], r["size"]))
            if r["size"] < 1000 and r["body"]:
                print("      %s" % r["body"][:200])

        try:
            body_text = await page.inner_text("body")
            lines = [l.strip() for l in body_text.split("\n") if l.strip()][:8]
            print("  Text: %s" % " | ".join(lines))
        except Exception:
            print("  (no body text)")

        for sel in ["table", "tr", ".card", "article", "[class*=tender]", "[class*=lot]"]:
            try:
                els = await page.query_selector_all(sel)
                if els:
                    print("  '%s': %d" % (sel, len(els)))
            except Exception:
                pass

        await browser.close()


async def main():
    pages = [
        ("https://beeline.uz/ru/about/tenderi", "Beeline"),
        ("https://uzsuv.uz/ru/tenderss", "Uzsuv"),
        ("https://uz.bnect.pro/procurement", "Bnect"),
        ("https://ssv.uz/ru/ssv/sections/tenderlar", "MinZdrav"),
        ("https://www.aiib.org/en/opportunities/business/project-procurement/list.html", "AIIB"),
        ("https://supply.unicef.org/rfx", "UNICEF"),
        ("https://www.utg.uz/ru/business/zakup/tendery/", "UTG"),
        ("https://agrobank.uz/ru/xarid-tender", "Agrobank"),
    ]
    for url, name in pages:
        try:
            await intercept_page(url, name, 8)
        except Exception as e:
            print("  FATAL: %s" % str(e)[:80])


asyncio.run(main())
