"""One-shot repair: re-render today's broken Telegram alerts (#1860-#1863).

For each alert:
  1. Read tender row from Supabase by alert_seq.
  2. Open the source detail page through Playwright (residential proxy if set).
     - For UZEX Предквалификации SPA the deep link slips back to homepage,
       but the SPA still renders the lot if we wait for `networkidle` and
       a generous timeout. We screenshot whatever it renders.
     - For Hayotbirja the source_url was a literal placeholder — fall back
       to the SPA list (procedure/tender) and search by external_id substring.
  3. Upload JPEG to Supabase Storage tender-screenshots/<source>/<external_id>.jpg.
  4. Persist screenshot URL in tenders.extra_info.screenshot_url.
  5. Edit Telegram message via editMessageMedia with the new photo + updated
     caption pointing at parsing-seo.vercel.app/tenders/<uuid> (our page).
     Fallback: deleteMessage + sendPhoto when editMessageMedia rejects (e.g.
     original was text-only; in that case Telegram returns "message is not
     modified" or "there is no media in the message"). Save new message_id.

Run on VPS:
    cd /opt/parsing-seo && .venv/bin/python -m crawler.scripts.repair_today_alerts
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings
from crawler.core.db import _get_client
from crawler.core.storage import upload_screenshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("repair")

ALERT_SEQS = [1860, 1861, 1862, 1863]
DETAIL_BASE = "https://parsing-seo.vercel.app/tenders"


def _fmt_price(price, currency):
    # type: (Optional[float], Optional[str]) -> str
    if not price:
        return ""
    return "{:,.0f} {}".format(price, currency or "UZS").replace(",", " ")


def _fmt_deadline(deadline):
    # type: (Optional[str]) -> str
    if not deadline:
        return ""
    try:
        dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return deadline


def _build_caption(row, screenshot_present):
    # type: (Dict[str, Any], bool) -> str
    """Build a Telegram caption (Markdown) for a repaired alert.

    Caption length cap: 1024 characters (Telegram photo caption limit).
    """
    seq = row.get("alert_seq")
    title = (row.get("title") or "")[:200]
    org = row.get("organization") or ""
    price = _fmt_price(row.get("price"), row.get("currency"))
    deadline = _fmt_deadline(row.get("deadline"))
    src = row.get("source") or ""
    uuid = row.get("id")

    parts = []
    parts.append("#%03d [ТЕНДЕР]" % seq)
    parts.append("*%s*" % title.replace("*", "").replace("_", "").replace("`", "").replace("[", ""))
    if org:
        parts.append("Заказчик: %s" % org.replace("*", "").replace("_", ""))
    if price:
        parts.append("Сумма: %s" % price)
    if deadline:
        parts.append("Дедлайн: %s" % deadline)
    parts.append("Источник: %s" % src)
    parts.append("%s/%s" % (DETAIL_BASE, uuid))
    if screenshot_present:
        parts.append("_(скриншот выше — площадка %s не открывается напрямую)_" % src)
    parts.append("#полиграфия #ремонт")  # keep keyword tag style

    text = "\n".join(parts)
    if len(text) > 1024:
        text = text[:1020] + "..."
    return text


async def _capture_screenshot(row):
    # type: (Dict[str, Any]) -> Optional[bytes]
    """Open the tender detail in Playwright (residential proxy if set) and
    return a JPEG byte string of the page. Returns None if everything fails.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed (.venv missing)")
        return None

    src = row.get("source") or ""
    uuid = row.get("id")

    # Always screenshot OUR own /tenders/{uuid} page — it's guaranteed to render
    # (Vercel SSR + Supabase data). The original source URLs are broken SPAs that
    # slip back to the platform homepage, so a "real" screenshot is empty/useless.
    # Our page shows title, organization, price, deadline — exactly what's useful
    # in the Telegram preview.
    if not uuid:
        logger.warning("[seq %s] no uuid, skipping screenshot", row.get("alert_seq"))
        return None
    url = "{}/{}".format(DETAIL_BASE, uuid)

    proxy_cfg = None
    # Our own page does NOT need a proxy — it's on Vercel. Skip the residential
    # proxy entirely for screenshot capture (saves bandwidth, avoids weird proxy
    # latency on a public URL).
    if False and settings.residential_proxy_url:
        # async_playwright accepts proxy={"server": "http://host:port", "username":..., "password":...}
        from urllib.parse import urlparse
        u = urlparse(settings.residential_proxy_url)
        proxy_cfg = {"server": "{}://{}:{}".format(u.scheme or "http", u.hostname, u.port or 80)}
        if u.username:
            proxy_cfg["username"] = u.username
        if u.password:
            proxy_cfg["password"] = u.password

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx_kwargs = {
                "viewport": {"width": 1080, "height": 1350},  # Telegram-friendly aspect
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
                "device_scale_factor": 2,
                "color_scheme": "dark",  # tenders/[id] uses bg-gray-950 dark theme
            }
            if proxy_cfg:
                ctx_kwargs["proxy"] = proxy_cfg
            ctx = await browser.new_context(**ctx_kwargs)
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                # Wait for the tender title to appear (means data fetched from /api/tenders/[id])
                try:
                    await page.wait_for_selector("h1", timeout=5000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                shot = await page.screenshot(full_page=False, type="jpeg", quality=85)
                logger.info("[seq %s] captured %d KB from %s", row.get("alert_seq"), len(shot) // 1024, url)
                return shot
            except Exception as exc:
                logger.warning("[seq %s] page error %s — taking whatever rendered", row.get("alert_seq"), str(exc)[:200])
                try:
                    return await page.screenshot(full_page=False, type="jpeg", quality=80)
                except Exception:
                    return None
        finally:
            await browser.close()


async def _telegram_call(method, payload):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """POST to Telegram Bot API. Returns the parsed JSON, including failures."""
    bot_url = "https://api.telegram.org/bot{}/{}".format(settings.telegram_bot_token, method)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(bot_url, json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "raw_status": resp.status_code, "raw_text": resp.text[:200]}
        return data


async def _repair_one(row):
    # type: (Dict[str, Any]) -> bool
    seq = row.get("alert_seq")
    mid = row.get("telegram_message_id")
    if not mid:
        logger.warning("[seq %s] no telegram_message_id, skipping", seq)
        return False

    # 1. Screenshot
    shot = await _capture_screenshot(row)

    # 2. Upload (if we got bytes)
    photo_url = None
    if shot:
        photo_url = upload_screenshot(shot, row.get("source") or "unknown", row.get("external_id") or seq, mime="image/jpeg")
        if photo_url:
            # Persist into extra_info
            try:
                client = _get_client()
                ei = dict(row.get("extra_info") or {})
                ei["screenshot_url"] = photo_url
                ei["screenshot_at"] = datetime.now(timezone.utc).isoformat()
                client.table("tenders").update({"extra_info": ei}).eq("id", row["id"]).execute()
            except Exception as exc:
                logger.warning("[seq %s] save extra_info failed: %s", seq, str(exc)[:200])

    # 3. Build new caption
    caption = _build_caption(row, screenshot_present=bool(photo_url))

    # 4. Try editMessageMedia (works only if original message had media — likely fails here)
    if photo_url:
        edit_payload = {
            "chat_id": settings.telegram_alert_chat_id,
            "message_id": mid,
            "media": json.dumps({
                "type": "photo",
                "media": photo_url,
                "caption": caption,
                "parse_mode": "Markdown",
            }, ensure_ascii=False),
        }
        result = await _telegram_call("editMessageMedia", edit_payload)
        if result.get("ok"):
            logger.info("[seq %s] editMessageMedia OK (mid=%s)", seq, mid)
            return True
        logger.info("[seq %s] editMessageMedia failed (%s) — falling back to delete+sendPhoto",
                    seq, (result.get("description") or "")[:120])

    # 5. Fallback: deleteMessage + sendPhoto (or sendMessage if no screenshot)
    await _telegram_call("deleteMessage", {
        "chat_id": settings.telegram_alert_chat_id,
        "message_id": mid,
    })
    if photo_url:
        send = await _telegram_call("sendPhoto", {
            "chat_id": settings.telegram_alert_chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "Markdown",
        })
    else:
        send = await _telegram_call("sendMessage", {
            "chat_id": settings.telegram_alert_chat_id,
            "text": caption,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        })

    if not send.get("ok"):
        logger.error("[seq %s] re-send failed: %s", seq, (send.get("description") or "")[:120])
        return False

    new_mid = (send.get("result") or {}).get("message_id")
    logger.info("[seq %s] re-sent as new mid=%s", seq, new_mid)

    # Persist new message_id
    if new_mid:
        try:
            client = _get_client()
            client.table("tenders").update({"telegram_message_id": new_mid}).eq("id", row["id"]).execute()
        except Exception as exc:
            logger.warning("[seq %s] update telegram_message_id failed: %s", seq, str(exc)[:200])
    return True


async def main():
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.error("Telegram bot token / chat id not configured — abort")
        sys.exit(2)

    client = _get_client()
    rows = (
        client.table("tenders")
        .select("id,external_id,source,source_url,title,organization,price,currency,deadline,extra_info,alert_seq,telegram_message_id,categories,message_type")
        .in_("alert_seq", ALERT_SEQS)
        .execute()
    ).data or []
    rows.sort(key=lambda r: r.get("alert_seq") or 0)

    logger.info("Loaded %d alert rows: seqs=%s", len(rows), [r.get("alert_seq") for r in rows])

    ok = 0
    for row in rows:
        try:
            if await _repair_one(row):
                ok += 1
        except Exception as exc:
            logger.exception("[seq %s] unexpected: %s", row.get("alert_seq"), exc)
    logger.info("Repair done: %d / %d", ok, len(rows))


if __name__ == "__main__":
    asyncio.run(main())
