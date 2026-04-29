"""Snap a screenshot of our own /tenders/{uuid} page for Telegram alerts.

Used by the notifier when the source platform is a broken SPA (link slips
to homepage / 404). Our SSR'd Next.js page renders title, organization,
price, deadline, period — and the snapshot survives even if the source
platform later removes the lot.

Output: JPEG bytes uploaded to Supabase Storage; URL stored in
tenders.extra_info.screenshot_url.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DETAIL_BASE = "https://parsing-seo.vercel.app/tenders"

# Sources whose deep-link is broken outside of authenticated browser sessions.
# Keep in sync with web/src/app/tenders/[id]/page.tsx BROKEN_SPA_HOSTS.
BROKEN_SPA_SOURCES = {
    "Hayotbirja отбор",
    "Hayotbirja встречные аукционы",
    "Hayotbirja э-магазин",
    "XT-Xarid встречные аукционы",
    "UZEX Предквалификации",
    "Xarid Конкурсы",
    "Xarid Прямые закупки",
}
BROKEN_SPA_PREFIXES = ("Cooperation.uz", "xt-xarid")


def is_broken_spa(source):
    # type: (str) -> bool
    if not source:
        return False
    if source in BROKEN_SPA_SOURCES:
        return True
    return any(source.startswith(p) for p in BROKEN_SPA_PREFIXES)


async def capture_our_page(uuid, viewport_w=1080, viewport_h=1350):
    # type: (str, int, int) -> Optional[bytes]
    """Open https://parsing-seo.vercel.app/tenders/{uuid} in headless Chromium
    and return a JPEG screenshot of the visible viewport.

    Returns None on any failure — the caller should fall back to text-only
    sendMessage.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("[snap] playwright not installed")
        return None

    if not uuid:
        return None

    url = "{}/{}".format(DETAIL_BASE, uuid)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = await browser.new_context(
                viewport={"width": viewport_w, "height": viewport_h},
                device_scale_factor=2,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
                color_scheme="dark",
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                try:
                    await page.wait_for_selector("h1", timeout=5000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                shot = await page.screenshot(full_page=False, type="jpeg", quality=85)
                logger.info("[snap] captured %s -> %d KB", uuid, len(shot) // 1024)
                return shot
            except Exception as exc:
                logger.warning("[snap] %s page error: %s", uuid, str(exc)[:200])
                try:
                    return await page.screenshot(full_page=False, type="jpeg", quality=80)
                except Exception:
                    return None
        finally:
            await browser.close()


async def snap_and_upload(uuid, source, external_id):
    # type: (str, str, str) -> Optional[str]
    """High-level helper: capture our page + upload to Supabase Storage.
    Returns the public URL or None.
    """
    from crawler.core.storage import upload_screenshot

    shot = await capture_our_page(uuid)
    if not shot:
        return None
    return upload_screenshot(shot, source or "unknown", external_id or uuid, mime="image/jpeg")
