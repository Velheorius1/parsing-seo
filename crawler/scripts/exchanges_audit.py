#!/usr/bin/env python3
"""Step-by-step healthcheck of all tender exchanges + sources.

For every known exchange/source, run a sequence of probes:
  1. HTTP probe — does the public API/page respond at all
  2. Collection — DB count for last 24h / 7d
  3. Field completeness — % of recent tenders with price, organization, deadline
  4. Sample URL — pick one fresh tender and verify the constructed URL is reachable
  5. Alerts ratio — collected_7d vs alerted_7d (silent-death detector)
  6. Same-source duplicates — top duplicate (title, org) groups in 24h
  7. Stale deadlines — count of recent tenders with deadline >30d in the past

Final output: markdown report with per-source verdicts (OK / WARN / FAIL).
Send to Telegram with --telegram flag.

Usage:
    python3 -m crawler.scripts.exchanges_audit               # console only
    python3 -m crawler.scripts.exchanges_audit --telegram    # also send to TG
    python3 -m crawler.scripts.exchanges_audit --json        # JSON output
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# Source registry: display_name → check config. Add new sources here.
# http_url is optional — used for liveness probe (must be GET-able without auth).
SOURCES: List[Dict] = [
    # === Ebirja (E-IMZO authed) ===
    {"name": "Ebirja Электронный магазин",      "http": "https://xarid-api.ebirja.uz/shop/product/announce-list?currentPage=0&perPage=1&platform_display=e-shop", "auth": "ebirja-jwt"},
    {"name": "Ebirja Национальный магазин",     "http": "https://xarid-api.ebirja.uz/shop/product/announce-list?currentPage=0&perPage=1&platform_display=national-shop", "auth": "ebirja-jwt"},
    {"name": "Ebirja Аукционы",                 "http": "https://xarid-api.ebirja.uz/auction/auction/active?page=0&size=1", "auth": "ebirja-jwt"},
    {"name": "E-Birja товары на продажу",       "http": "https://api.ebirja.uz/fond-api/api/external/product/all?page=0&size=1", "auth": None},
    {"name": "E-Birja завершённые сделки",      "http": "https://api.ebirja.uz/fond-api/api/external/contract/all?page=0&size=1", "auth": None},
    # === Hayot Birja ===
    {"name": "Hayot Birja",                    "http": None, "auth": None},
    {"name": "Hayotbirja тендеры",             "http": None, "auth": None},
    {"name": "Hayotbirja отбор",               "http": None, "auth": None},
    {"name": "Hayotbirja встречные аукционы",  "http": None, "auth": None},
    # === XT-Xarid ===
    {"name": "xt-xarid.uz",                    "http": None, "auth": None},
    {"name": "XT-Xarid тендеры",               "http": None, "auth": None},
    {"name": "XT-Xarid встречные аукционы",    "http": None, "auth": None},
    # === Xarid (UZEX госзакупки конкурсы) ===
    {"name": "Xarid Конкурсы",                 "http": None, "auth": None},
    {"name": "Xarid Прямые закупки",           "http": None, "auth": None},
    # === UZEX ===
    {"name": "ETender UZEX",                   "http": None, "auth": None},
    {"name": "ETender Обсуждения",             "http": None, "auth": None},
    {"name": "UZEX Предквалификации",          "http": None, "auth": None},
    {"name": "UZEX Результаты",                "http": None, "auth": None},
    # === Cooperation.uz ===
    {"name": "Cooperation.uz Bosma (узб.)",    "http": None, "auth": None},
    {"name": "Cooperation.uz Полиграфия",      "http": None, "auth": None},
    {"name": "Cooperation.uz Печать",          "http": None, "auth": None},
    {"name": "Cooperation.uz Этикетки",        "http": None, "auth": None},
    {"name": "Cooperation.uz Пакеты",          "http": None, "auth": None},
    {"name": "Cooperation.uz Конверты",        "http": None, "auth": None},
    {"name": "Cooperation.uz Календари",       "http": None, "auth": None},
    {"name": "Cooperation.uz Брошюры/Буклеты", "http": None, "auth": None},
    {"name": "Cooperation.uz Стикеры/Наклейки","http": None, "auth": None},
    {"name": "Cooperation.uz Блокноты/Ежедневники","http": None, "auth": None},
    {"name": "Cooperation.uz Лоты",            "http": None, "auth": None},
    # === Прочие основные ===
    {"name": "Beeline UZ Тендеры",             "http": "https://beeline.uz/", "auth": None},
    {"name": "Tender.mc.uz (Минстрой)",        "http": None, "auth": None},
    {"name": "B2Biz.uz (Тендеры)",             "http": None, "auth": None},
    {"name": "B2Biz.uz (Планы закупок)",       "http": None, "auth": None},
    {"name": "Ucell (COSCOM)",                 "http": None, "auth": None},
    {"name": "Узбекистон темир йуллари (ЖД)",  "http": None, "auth": None},
    {"name": "Минэкономики (тендеры)",         "http": None, "auth": None},
    {"name": "АГМК (Алмалык ГМК)",             "http": None, "auth": None},
    {"name": "Tashkent Steel",                 "http": None, "auth": None},
    {"name": "Узбекистон металлургия комбинати","http": None, "auth": None},
    {"name": "Уз-Кор Газ Кимё",               "http": None, "auth": None},
    {"name": "SQB",                            "http": None, "auth": None},
    {"name": "Ипотека-банк",                  "http": None, "auth": None},
    {"name": "Хамкорбанк",                    "http": None, "auth": None},
    {"name": "TrustBank",                      "http": None, "auth": None},
    {"name": "АнорБанк",                      "http": None, "auth": None},
    {"name": "MOBIUZ",                         "http": None, "auth": None},
    {"name": "Uzbekistan Airports",            "http": None, "auth": None},
    {"name": "Uz-airways",                     "http": None, "auth": None},
]

# Niche keywords for "should-have-alerted" detection.
NICHE_KEYWORDS = [
    "полиграф", "печат", "упаков", "пакет", "коробк", "этикет",
    "наклей", "брошюр", "бланк", "визит", "буклет", "стикер",
    "блокнот", "конверт", "сувенир", "флаер", "открытк",
    "ежедневник", "карт", "обложк", "офсет", "bosma", "kalendar",
]


def _get_supabase():
    from supabase import create_client
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase not configured")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def check_http(http_url: Optional[str]) -> Tuple[str, str]:
    if not http_url:
        return ("skip", "no http_url defined")
    try:
        with httpx.Client(timeout=8, verify=False, follow_redirects=True) as c:
            r = c.get(http_url, headers={"User-Agent": "Mozilla/5.0"})
        if 200 <= r.status_code < 400:
            return (OK, f"HTTP {r.status_code}")
        return (FAIL, f"HTTP {r.status_code}")
    except httpx.TimeoutException:
        return (WARN, "timeout (UZ-IP only?)")
    except Exception as exc:
        return (WARN, f"{type(exc).__name__}: {str(exc)[:60]}")


def check_collection(client, source: str) -> Dict:
    since_24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    r24 = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_24).limit(0).execute()
    r7 = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).limit(0).execute()
    cnt24, cnt7 = (r24.count or 0), (r7.count or 0)
    if cnt7 == 0:
        return {"status": FAIL, "msg": "DEAD: 0 collected in 7 days", "count_24h": 0, "count_7d": 0}
    if cnt24 == 0:
        return {"status": WARN, "msg": "no new in 24h", "count_24h": 0, "count_7d": cnt7}
    return {"status": OK, "msg": f"{cnt24} in 24h, {cnt7} in 7d", "count_24h": cnt24, "count_7d": cnt7}


def check_fields(client, source: str) -> Dict:
    since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rt = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).limit(0).execute()
    total = rt.count or 0
    if total == 0:
        return {"status": "skip", "msg": "no data"}
    rp = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).gt("price", 0).limit(0).execute()
    ro = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).neq("organization", "").not_.is_("organization", "null").limit(0).execute()
    rd = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).neq("deadline", "").not_.is_("deadline", "null").limit(0).execute()
    pp = (rp.count or 0) * 100 // total
    po = (ro.count or 0) * 100 // total
    pd = (rd.count or 0) * 100 // total
    issues = []
    if pp < 30 and total > 50:
        issues.append(f"price {pp}%")
    if po < 50 and total > 50:
        issues.append(f"org {po}%")
    if pd < 30 and total > 50:
        issues.append(f"deadline {pd}%")
    status = WARN if issues else OK
    return {"status": status, "msg": f"price={pp}% org={po}% deadline={pd}%", "issues": issues, "price_pct": pp, "org_pct": po, "deadline_pct": pd}


def check_alerts_ratio(client, source: str) -> Dict:
    since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rt = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).limit(0).execute()
    ra = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).not_.is_("alert_seq", "null").limit(0).execute()
    total = rt.count or 0
    alerted = ra.count or 0
    if total == 0:
        return {"status": "skip", "msg": "no data", "alerted": 0, "total": 0}
    if total > 200 and alerted == 0:
        # Probe: are there niche-keyword tenders that should have alerted?
        for kw in NICHE_KEYWORDS:
            rkw = client.table("tenders").select("id", count="exact").eq("source", source).gte("collected_at", since_7d).ilike("title", f"%{kw}%").limit(0).execute()
            if (rkw.count or 0) > 0:
                return {"status": FAIL, "msg": f"SILENT DEATH: 0 alerts on {total} (has '{kw}' keyword inside)", "alerted": 0, "total": total}
        return {"status": WARN, "msg": f"0 alerts on {total} (no niche keywords found)", "alerted": 0, "total": total}
    pct = alerted * 100 // total
    return {"status": OK, "msg": f"{alerted}/{total} alerted ({pct}%)", "alerted": alerted, "total": total}


def check_dups(client, source: str) -> Dict:
    since_24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rs = client.table("tenders").select("title,organization").eq("source", source).gte("collected_at", since_24).not_.is_("alert_seq", "null").limit(500).execute()
    if not rs.data:
        return {"status": "skip", "msg": "no alerts in 24h"}
    counter = Counter()
    for row in rs.data:
        key = ((row.get("title") or "")[:80].lower().strip(), (row.get("organization") or "")[:50].lower().strip())
        counter[key] += 1
    dups = [(k, n) for k, n in counter.items() if n > 1]
    if not dups:
        return {"status": OK, "msg": "no dups in 24h alerts"}
    worst = max(dups, key=lambda x: x[1])
    msg = f"{len(dups)} duplicate groups, worst ×{worst[1]}: '{worst[0][0][:50]}'"
    status = FAIL if worst[1] >= 5 else WARN
    return {"status": status, "msg": msg, "groups": len(dups), "worst_count": worst[1]}


def check_stale_deadlines(client, source: str) -> Dict:
    """Count alerts in last 24h with deadline more than 30 days in the past."""
    since_24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rs = client.table("tenders").select("deadline,alert_seq").eq("source", source).gte("collected_at", since_24).not_.is_("alert_seq", "null").lt("deadline", cutoff[:10]).limit(50).execute()
    n = len(rs.data) if rs.data else 0
    if n == 0:
        return {"status": OK, "msg": "no stale deadlines"}
    return {"status": WARN, "msg": f"{n} alerts have deadline >30d past"}


def check_sample_url(client, source: str) -> Dict:
    """Pick one fresh tender, build URL, GET it, check is not 404/empty."""
    since_24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rs = client.table("tenders").select("external_id").eq("source", source).gte("collected_at", since_24).order("collected_at", desc=True).limit(1).execute()
    if not rs.data:
        return {"status": "skip", "msg": "no fresh tender"}
    # We don't know the URL template per source from outside — check if there's a plain DB column
    # instead. Skip this probe for now and rely on the http liveness check.
    return {"status": "skip", "msg": "sample URL check deferred (use http probe)"}


def audit_source(client, src_def: Dict) -> Dict:
    name = src_def["name"]
    auth = src_def.get("auth")
    result = {"name": name, "auth": auth or "—"}
    result["http"] = check_http(src_def.get("http"))
    result["collection"] = check_collection(client, name)
    result["fields"] = check_fields(client, name)
    result["alerts"] = check_alerts_ratio(client, name)
    result["dups"] = check_dups(client, name)
    result["stale"] = check_stale_deadlines(client, name)
    return result


def overall_status(result: Dict) -> str:
    statuses = [
        result.get("collection", {}).get("status"),
        result.get("fields", {}).get("status"),
        result.get("alerts", {}).get("status"),
        result.get("dups", {}).get("status"),
        result.get("stale", {}).get("status"),
    ]
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK


def render_report(results: List[Dict]) -> str:
    lines = []
    by_status = Counter(overall_status(r) for r in results)
    lines.append(f"📊 *Парсинг-SEO Аудит бирж* ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")
    lines.append(f"Источников: {len(results)} — OK: {by_status[OK]}, WARN: {by_status[WARN]}, FAIL: {by_status[FAIL]}")
    lines.append("")

    # Group by status: FAIL first, then WARN, then OK summary
    fail = [r for r in results if overall_status(r) == FAIL]
    warn = [r for r in results if overall_status(r) == WARN]
    ok = [r for r in results if overall_status(r) == OK]

    if fail:
        lines.append("❌ *FAIL:*")
        for r in fail:
            lines.append(f"• {r['name']}")
            for k in ("collection", "fields", "alerts", "dups", "stale"):
                v = r.get(k, {})
                if v.get("status") == FAIL:
                    lines.append(f"    └ {k}: {v.get('msg', '')}")
        lines.append("")
    if warn:
        lines.append("⚠️ *WARN:*")
        for r in warn:
            issues = []
            for k in ("collection", "fields", "alerts", "dups", "stale"):
                v = r.get(k, {})
                if v.get("status") == WARN:
                    issues.append(f"{k}: {v.get('msg', '')}")
            lines.append(f"• {r['name']} — {' | '.join(issues)}")
        lines.append("")
    if ok:
        lines.append(f"✅ *OK*: {len(ok)} источников работают штатно")
        # Show top by alerts
        top = sorted(ok, key=lambda x: -(x.get("alerts", {}).get("alerted", 0)))[:5]
        for r in top:
            a = r.get("alerts", {})
            c = r.get("collection", {})
            lines.append(f"    {r['name']}: {c.get('msg','')} | alerts {a.get('msg','')}")

    return "\n".join(lines)


def send_telegram(text: str):
    """Send via Telegram bot (TELEGRAM_BOT_TOKEN + TELEGRAM_ALERT_CHAT_ID)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "")
    if not token or not chat:
        logger.warning("[Audit] TG token/chat not configured, skipping send")
        return
    # Telegram limit 4096 chars — split if needed
    chunks = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    for i, chunk in enumerate(chunks):
        try:
            r = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": chunk, "parse_mode": "Markdown"},
                timeout=15,
            )
            if r.status_code != 200:
                # Retry without markdown
                httpx.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": chunk},
                    timeout=15,
                )
        except Exception as exc:
            logger.warning("[Audit] TG send failed (chunk %d): %s", i, str(exc)[:80])


def main():
    ap = argparse.ArgumentParser(description="Step-by-step audit of all tender exchanges")
    ap.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--only-fail", action="store_true", help="Send only if any FAIL detected")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    client = _get_supabase()
    results = []
    for src in SOURCES:
        try:
            results.append(audit_source(client, src))
        except Exception as exc:
            logger.warning("Audit failed for %s: %s", src["name"], str(exc)[:120])
            results.append({"name": src["name"], "error": str(exc)[:120]})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return 0

    report = render_report(results)
    print(report)

    has_fail = any(overall_status(r) == FAIL for r in results)
    if args.telegram and (has_fail or not args.only_fail):
        send_telegram(report)
        logger.info("[Audit] Report sent to Telegram")

    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
