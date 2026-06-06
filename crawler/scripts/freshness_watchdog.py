"""Freshness-SLO watchdog — alerts when a LIVE source goes unexpectedly silent.

Gap this fills: zero_result_tracker only flags sources that RAN and returned 0 this
cycle. A source whose fetcher was removed or whose upstream silently died (e.g.
Cooperation.uz Bosma — 993 rows, silent 40 days, noticed only in a manual audit)
never appears in crawl outcomes, so nothing fires. This watchdog is DB-based: it
compares each source's max(collected_at) against a silence threshold.

Signal-not-noise design:
- KNOWN_RETIRED allowlist suppresses sources confirmed dead-by-design (the 9
  Cooperation printing feeds consolidated into 'Cooperation.uz Лоты' on 27.04,
  plus orphan duplicate connectors). Audit 2026-06-06 verified replaced-not-lost.
- Only sources with >= MIN_ROWS history are considered (ignores tiny/new feeds).
- State in crawler_settings (session_store) — alert once per newly-silent source,
  recovery message once when it returns. No alert storms.

Cron (host): 0 7 * * * cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.freshness_watchdog
Usage: --dry-run (print, no Telegram/state write).
"""
import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Set

import httpx

from crawler.auth.session_store import session_store
from crawler.config.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("freshness-watchdog")

STATE_KEY = "freshness_watchdog_state"
SILENCE_DAYS = 7
MIN_ROWS = 20

# Sources confirmed dead-by-design (audit 2026-06-06: replaced-not-lost — their
# products now flow through the live unified feeds). Do NOT alert on these.
KNOWN_RETIRED = frozenset({
    "Cooperation.uz Пакеты", "Cooperation.uz Блокноты/Ежедневники",
    "Cooperation.uz Полиграфия", "Cooperation.uz Конверты",
    "Cooperation.uz Стикеры/Наклейки", "Cooperation.uz Календари",
    "Cooperation.uz Этикетки", "Cooperation.uz Печать",
    "Cooperation.uz Брошюры/Буклеты", "Cooperation.uz Bosma (узб.)",
    "Минстрой (tender.mc.uz)", "E-Birja активные аукционы (xarid)",
})


def _supabase():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _silent_sources():
    # type: () -> List[Dict]
    """Return sources silent >= SILENCE_DAYS with >= MIN_ROWS history, excluding retired."""
    client = _supabase()
    rows = (client.rpc("source_freshness").execute().data) or []
    now = datetime.now(timezone.utc)
    out = []  # type: List[Dict]
    for r in rows:
        src = r.get("source") or ""
        cnt = r.get("cnt") or 0
        last = r.get("last_collected")
        if not last or cnt < MIN_ROWS or src in KNOWN_RETIRED:
            continue
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        days = (now - last_dt).days
        if days >= SILENCE_DAYS:
            out.append({"source": src, "cnt": int(cnt), "days": days, "last": last[:10]})
    out.sort(key=lambda x: -x["days"])
    return out


async def _send_telegram(text):
    # type: (str) -> bool
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("No telegram config — skipping send")
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.post(url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text, "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Telegram send failed: %s", str(exc)[:120])
        return False


async def main(dry_run=False):
    # type: (bool) -> int
    silent = _silent_sources()
    silent_names = {s["source"] for s in silent}  # type: Set[str]

    raw_state = session_store.get_setting(STATE_KEY) if not dry_run else None
    prev = set(raw_state.get("silent", [])) if isinstance(raw_state, dict) else set()

    new_silent = [s for s in silent if s["source"] not in prev]
    revived = sorted(prev - silent_names)

    logger.info("Silent>%dd: %d (new: %d, revived: %d)",
                SILENCE_DAYS, len(silent), len(new_silent), len(revived))
    for s in silent:
        logger.info("   %s — %d rows, silent %dd (last %s)%s",
                    s["source"], s["cnt"], s["days"], s["last"],
                    "  [NEW]" if s["source"] not in prev else "")

    if dry_run:
        logger.info("DRY RUN — no Telegram, no state write")
        return 0

    if new_silent:
        lines = ["\U0001f507 *Источник замолчал* (freshness-SLO >%dд):" % SILENCE_DAYS]
        for s in new_silent:
            lines.append("• *%s* — молчит %dд (последний %s, было %d строк)" %
                         (s["source"], s["days"], s["last"], s["cnt"]))
        lines.append("\n_Проверь фетчер/upstream — источник давал данные, но перестал._")
        await _send_telegram("\n".join(lines))

    if revived:
        await _send_telegram("\U0001f50a *Источник ожил*: %s" % ", ".join(revived))

    session_store.set_setting(STATE_KEY, {"silent": sorted(silent_names),
                                          "updated_at": datetime.now(timezone.utc).isoformat()})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(dry_run=args.dry_run)))
