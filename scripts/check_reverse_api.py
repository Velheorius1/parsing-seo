"""Intercept API responses from ebirja reverse-auction page."""
import asyncio
import json
from playwright.async_api import async_playwright


async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        responses_data = []

        async def on_response(resp):
            url = resp.url.lower()
            if "api" in url and ("auction" in url or "reverse" in url or "fond" in url or "trade" in url):
                try:
                    body = await resp.text()
                    responses_data.append({
                        "url": resp.url,
                        "status": resp.status,
                        "body": body[:3000],
                    })
                except Exception:
                    responses_data.append({
                        "url": resp.url,
                        "status": resp.status,
                        "body": "<error reading>",
                    })

        page.on("response", on_response)

        print("Navigating...")
        await page.goto("https://ebirja.uz/ru/trade/reverse-auction", timeout=30000)
        await asyncio.sleep(10)

        print("\n=== API RESPONSES ===")
        for r in responses_data:
            print("\nURL: %s" % r["url"])
            print("STATUS: %s" % r["status"])
            print("BODY (first 1500): %s" % r["body"][:1500])
            print("---")

        # Also check what's rendered
        print("\n=== PAGE ARTICLES ===")
        articles = await page.query_selector_all("article")
        for i, art in enumerate(articles[:5]):
            text = await art.inner_text()
            print("Article %d: %s" % (i, text[:200]))

        await browser.close()


asyncio.run(check())
