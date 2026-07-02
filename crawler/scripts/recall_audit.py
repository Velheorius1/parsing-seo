"""Nightly recall audit (V3, design 2026-07-02) — «мы не видим тендеры» killer.

Two measurements + one heal, all deterministic (workflows-over-agents):

1. MISSED (recall): active, never-alerted, in-scope lots in biddable sources
   (the backfill_recall.py C1 pattern, made recurring with a dynamic date).
   Candidates go through the NORMAL pipeline — send_alerts applies keyword → AI
   relevance → pre-send verification → push/digest routing — so a healed lot
   gets the same quality gates as a crawled one. Healed alerts carry a
   «🔁 найден ночным аудитом» line.
2. STALE (precision of what we already sent): a random sample of recently
   alerted still-active rows is re-verified on-platform via crawler.core.verifier
   (V1). closed/gone count = measured stale-rate of delivered alerts.
3. REPORT: Telegram only when healed>0 or stale>threshold (silent when clean,
   per noise policy). ALWAYS appends one JSON line to logs/recall_audit.jsonl —
   the weekly routine turns this into a MEASURED recall sub-score.

Cron: 30 3 * * * (after the 22:00 crawl, before the 06:00 one).
Usage: python3 -m crawler.scripts.recall_audit [--execute] [--stale-sample N]
Default is DRY-RUN (prints what would happen, no sends, no jsonl).
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

from crawler.config.settings import settings
from crawler.core.models import RawTender

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("recall_audit")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSONL = os.path.join(REPO_ROOT, "logs", "recall_audit.jsonl")

# Biddable buyer-bearing sources (mirrors backfill_recall C1 set).
SOURCES = [
    "B2Biz.uz (Тендеры)", "B2Biz.uz (Планы закупок)",
    "Hayotbirja отбор", "Hayotbirja встречные аукционы", "Hayotbirja тендеры",
    "ETender UZEX", "ETender Обсуждения",
    "Xarid Конкурсы", "Xarid Прямые закупки",
    "Tender.mc.uz (Минстрой)", "UZEX Предквалификации", "UZEX Обратные аукционы",
    "XT-Xarid встречные аукционы", "XT-Xarid тендеры", "XT-Xarid запросы предложений",
]

# STRONG printing-product regex (C1-proven: high precision, no filler words).
STRONG = (
    "печат|полиграф|типограф|bosma|chop etish|блокнот|ежедневник|тетрад|daftar|"
    "конверт|konvert|бланк|каталог|katalog|брошюр|буклет|buklet|картон|karton|гофр|"
    "коробк|упаков|qadoq|этикет|yorliq|наклейк|стикер|бейдж|табличк|выставочн|"
    "информацион.{0,12}стенд|стенд.{0,12}лдсп|календар|kalendar|открытк|визитк|vizitka|"
    "плакат|постер|издат|изделия из бумаг|чек.{0,4}лент|kitob"
)

FIELDS = ("id,external_id,title,organization,price,currency,deadline,date_start,date_end,"
          "source,source_url,status,search_text,message_type,extra_info,relevance_score")

STALE_TG_THRESHOLD = 5  # stale sample hits above this → Telegram warning


def _client():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _to_tender(r):
    return RawTender(
        id=r.get("id") or r.get("external_id"), external_id=r.get("external_id") or "",
        title=r.get("title") or "", organization=r.get("organization") or "",
        price=r.get("price"), currency=r.get("currency") or "UZS",
        deadline=r.get("deadline"), date_start=r.get("date_start"), date_end=r.get("date_end"),
        source=r.get("source") or "", source_url=r.get("source_url") or "",
        status=r.get("status") or "active", search_text=r.get("search_text") or "",
        message_type=r.get("message_type") or "tender",
        extra_info=r.get("extra_info") if isinstance(r.get("extra_info"), dict) else {},
    )


def _pull_unalerted(c, today):
    """Recent (14d, index-scan via idx_tenders_source_collected) never-alerted rows;
    deadline>=today filtered client-side — a text-column deadline filter server-side
    seq-scans and hits statement_timeout 57014 on big sources."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    rows = []
    for src in SOURCES:
        off = 0
        while True:
            q = (c.table("tenders").select(FIELDS).eq("source", src)
                 .is_("alert_seq", "null").gte("collected_at", since)
                 .order("collected_at", desc=True)
                 .range(off, off + 999).execute())
            d = q.data or []
            rows.extend(r for r in d if (r.get("deadline") or "") >= today)
            if len(d) < 1000 or off >= 4000:
                break
            off += 1000
    return rows


async def _stale_sample(c, n):
    """Re-verify a random sample of recently-alerted, still-active alerts."""
    import random
    from crawler.core.verifier import verify_lot, CLOSED, GONE
    import httpx
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = (c.table("tenders")
            .select("external_id,source,source_url,title,deadline")
            .not_.is_("alert_seq", "null").gte("deadline", today)
            .order("alert_seq", desc=True).limit(200).execute().data) or []
    # Only sources the verifier can actually check (birja/etender/prequest).
    checkable = [r for r in rows if r.get("source", "").startswith(("XT-Xarid", "Hayotbirja", "ETender"))
                 or r.get("source") == "UZEX Предквалификации"]
    sample = random.sample(checkable, min(n, len(checkable)))
    stale = []
    async with httpx.AsyncClient(timeout=10) as cl:
        for r in sample:
            t = _to_tender({**r, "id": r["external_id"]})
            try:
                v = await verify_lot(t, cl)
            except Exception:
                continue
            if v.status in (CLOSED, GONE):
                stale.append("%s | %s" % ((r.get("title") or "")[:36], r.get("source", "")[:22]))
    return len(sample), stale


async def _send_tg(text):
    import httpx
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return False
    async with httpx.AsyncClient(timeout=15) as cl:
        r = await cl.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                          json={"chat_id": settings.telegram_alert_chat_id, "text": text,
                                "parse_mode": "Markdown", "disable_web_page_preview": True})
    return r.status_code == 200


async def main(execute, stale_n):
    from crawler.core.notifier import send_alerts
    c = _client()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rx = re.compile(STRONG, re.I)

    # 1. MISSED: active never-alerted in-scope
    rows = _pull_unalerted(c, today)
    cands = [_to_tender(r) for r in rows
             if rx.search((r.get("title") or "") + " " + (r.get("search_text") or ""))]
    for t in cands:
        t.extra_info["Найден"] = "🔁 ночным аудитом"
    logger.info("[Recall] active never-alerted: %d | strong-keyword candidates: %d", len(rows), len(cands))
    healed = 0
    if cands:
        healed = await send_alerts(cands, dry_run=(not execute))

    # 2. STALE sample
    sampled, stale = await _stale_sample(c, stale_n)
    logger.info("[Recall] stale sample: %d/%d closed-or-gone", len(stale), sampled)

    # 3. Report + jsonl
    line = {"date": today, "ts": datetime.now(timezone.utc).isoformat(),
            "active_unalerted": len(rows), "candidates": len(cands), "healed": healed,
            "stale_sampled": sampled, "stale_found": len(stale), "dry_run": (not execute)}
    if execute:
        try:
            os.makedirs(os.path.dirname(JSONL), exist_ok=True)
            with open(JSONL, "a") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except IOError as exc:
            logger.warning("[Recall] jsonl write failed: %s", exc)
        if healed > 0 or len(stale) > STALE_TG_THRESHOLD:
            msg = ["🌙 *Ночной recall-аудит*"]
            if healed:
                msg.append("🔁 Найдено и доставлено пропущенных лотов: *%d* (шли обычным пайплайном: AI+верификация)" % healed)
            if len(stale) > STALE_TG_THRESHOLD:
                msg.append("⚠️ Stale в выборке: %d/%d уже закрыты на площадке:" % (len(stale), sampled))
                msg.extend("  • " + s for s in stale[:5])
            await _send_tg("\n".join(msg))
    print(json.dumps(line, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="send alerts + write jsonl (default dry-run)")
    ap.add_argument("--stale-sample", type=int, default=20)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.execute, a.stale_sample)))
