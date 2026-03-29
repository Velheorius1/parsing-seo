"""One-time E-IMZO token extraction script.

Run this on Mac when ebirja.uz JWT expires:
    python3 crawler/auth_eimzo.py

Two modes:
1. Manual paste — copy token from browser DevTools
2. Auto extract — opens browser, you login via E-IMZO, script captures token

Token is saved to Supabase crawler_settings table.
"""

import asyncio
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PLATFORMS = {
    "ebirja": {
        "name": "E-Birja (Tashkent Exchange)",
        "url": "https://app.ebirja.uz",
        "token_key": "exchange-web-token",
        "ttl_hours": 5,
    },
}


def manual_paste(platform_id):
    # type: (str) -> None
    """Paste token from browser DevTools."""
    from crawler.auth.session_store import session_store
    from datetime import datetime, timezone, timedelta

    platform = PLATFORMS[platform_id]
    print("\n--- Manual Token Paste ---")
    print("1. Open %s in browser" % platform["url"])
    print("2. Login via E-IMZO if needed")
    print("3. Open DevTools (F12) > Console")
    print("4. Run: localStorage.getItem('%s')" % platform["token_key"])
    print("5. Copy the token (without quotes)")
    print()

    token = input("Paste token: ").strip().strip('"').strip("'")
    if not token or len(token) < 20:
        print("ERROR: Invalid token")
        return

    # Calculate expiry
    ttl = platform.get("ttl_hours", 5)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()

    ok = session_store.set_token(platform_id, token, expires_at, source="manual")
    if ok:
        print("\nToken saved for %s" % platform["name"])
        print("Expires: %s (~%dh)" % (expires_at, ttl))
    else:
        print("\nERROR: Failed to save token")


async def auto_extract(platform_id):
    # type: (str) -> None
    """Open browser, wait for E-IMZO login, capture token."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: Install playwright first: pip install playwright && playwright install chromium")
        return

    from crawler.auth.session_store import session_store
    from datetime import datetime, timezone, timedelta

    platform = PLATFORMS[platform_id]
    print("\nOpening %s..." % platform["url"])
    print("Login via E-IMZO in the browser window.")
    print("The script will capture the token automatically.\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(platform["url"])

        # Wait for token to appear in localStorage (poll every 2s, max 5min)
        token = None
        for _ in range(150):
            await asyncio.sleep(2)
            try:
                token = await page.evaluate(
                    "localStorage.getItem('%s')" % platform["token_key"]
                )
                if token and len(token) > 20:
                    break
            except Exception:
                pass

        await browser.close()

        if not token:
            print("ERROR: No token found after 5 minutes. Did you login?")
            return

        ttl = platform.get("ttl_hours", 5)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()

        ok = session_store.set_token(platform_id, token, expires_at, source="auto-playwright")
        if ok:
            print("\nToken captured and saved for %s" % platform["name"])
            print("Expires: %s (~%dh)" % (expires_at, ttl))
        else:
            print("\nERROR: Failed to save token")


def main():
    # type: () -> None
    print("=== E-IMZO Token Extractor ===\n")

    # Platform selection
    print("Platforms:")
    ids = list(PLATFORMS.keys())
    for i, pid in enumerate(ids):
        print("  %d. %s" % (i + 1, PLATFORMS[pid]["name"]))
    choice = input("\nChoose platform [1]: ").strip() or "1"
    try:
        platform_id = ids[int(choice) - 1]
    except (IndexError, ValueError):
        print("Invalid choice")
        return

    # Mode selection
    print("\nMode:")
    print("  1. Paste token from DevTools (simple)")
    print("  2. Open browser, auto-capture (needs Playwright)")
    mode = input("\nChoose mode [1]: ").strip() or "1"

    if mode == "1":
        manual_paste(platform_id)
    elif mode == "2":
        asyncio.run(auto_extract(platform_id))
    else:
        print("Invalid mode")


if __name__ == "__main__":
    main()
