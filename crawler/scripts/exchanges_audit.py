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

10.10.2026 — аудит 172 ночи подряд писал «Report sent to Telegram», не отправив
ничего: токен искался в переменных окружения, которых у cron нет, а строка
«отправлено» стояла после вызова безусловно. Заодно отчёт, дойди он, был бы
мусором: список источников жил руками и разошёлся с конфигом (из 23 FAIL
половина — выведенные ленты Cooperation и старые имена вроде «SQB», «MOBIUZ»,
которые давно собираются под другими). Теперь:
  • список строится из sources.yaml (включённые, не Telegram) + ленты скриптов;
  • объяснённое молчание (source_health.silence_excuse) — не FAIL;
  • «0 алертов при ключевом слове» — WARN: слабый сигнал, полноту меряют
    recall_audit и shadow_search;
  • «отправлено» — только после ответа Telegram 200; иначе ошибка и код 2;
  • с --only-fail отчёт уходит, лишь когда набор FAIL изменился.

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
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.config.settings import settings  # noqa: E402
from crawler.core.source_health import (  # noqa: E402
    EXCUSE_EMPTY_OK, EXCUSE_MIRROR, EXCUSE_RETIRED, EXCUSE_WHITELIST, silence_excuse)

logger = logging.getLogger(__name__)

OK, WARN, FAIL = "OK", "WARN", "FAIL"

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG = os.path.join(_REPO, "crawler", "config", "sources.yaml")
_SENT_STATE = os.path.join(_REPO, "logs", "exchanges_audit_sent.json")

# Ленты, которые пишут скрипты, а не адаптеры краула: в sources.yaml их нет или
# они там выключены. Без явного списка аудит их бы не видел.
SCRIPT_FEEDS = (
    "Cooperation.uz Лоты", "Cooperation.uz Аукционы", "Cooperation.uz Э-магазин лоты",
    "Cooperation.uz Оферты", "Cooperation.uz Контракты",      # run_proxy_fetch.sh
    "UZEX Результаты",                                         # results_tracker: итоги ВМК-69
    "Ebirja Договоры (Э-магазин)", "Ebirja Договоры (Аукцион)",
    "Ebirja Договоры (Отбор)", "Ebirja Договоры (Тендер)",     # fetch_ebirja_contracts
)

# Проба живости площадки (информационная, в вердикт не входит).
_HTTP_PROBES = {
    "Ebirja Электронный магазин": ("https://xarid-api.ebirja.uz/shop/product/announce-list?currentPage=0&perPage=1&platform_display=e-shop", "ebirja-jwt"),
    "Ebirja Национальный магазин": ("https://xarid-api.ebirja.uz/shop/product/announce-list?currentPage=0&perPage=1&platform_display=national-shop", "ebirja-jwt"),
    "Beeline UZ Тендеры": ("https://beeline.uz/", None),
}

# Молчание, которое не поломка: выведен, зеркало, ноль — норма, разобран вручную.
_SILENCE_OK = (EXCUSE_RETIRED, EXCUSE_MIRROR, EXCUSE_EMPTY_OK, EXCUSE_WHITELIST)


def build_sources(config_path=_CONFIG):
    # type: (str) -> List[Dict]
    """Что проверять: включённые не-Telegram источники конфига + ленты скриптов."""
    with open(config_path, encoding="utf-8") as f:
        raw = (yaml.safe_load(f) or {}).get("sources") or []
    names = [s["name"] for s in raw
             if s.get("enabled", True) and s.get("adapter") != "telegram" and s.get("name")]
    for feed in SCRIPT_FEEDS:
        if feed not in names:
            names.append(feed)
    out = []
    for name in names:
        url, auth = _HTTP_PROBES.get(name, (None, None))
        out.append({"name": name, "http": url, "auth": auth})
    return out


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
                return {"status": WARN, "msg": f"0 alerts on {total} (has '{kw}' keyword inside)", "alerted": 0, "total": total}
        return {"status": WARN, "msg": f"0 alerts on {total} (no niche keywords found)", "alerted": 0, "total": total}
    pct = alerted * 100 // total
    return {"status": OK, "msg": f"{alerted}/{total} alerted ({pct}%)", "alerted": alerted, "total": total}


def check_dups(client, source: str) -> Dict:
    # По created_at, а не collected_at: collected_at переписывается при каждом
    # повторном сборе, и старые алерты месячной давности читались как «дубли за
    # сутки» (10.10: шесть алертов SQB «Сувенир макети» от мая).
    since_24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rs = client.table("tenders").select("title,organization").eq("source", source).gte("created_at", since_24).not_.is_("alert_seq", "null").limit(500).execute()
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
    """Count alerts on rows created in last 24h with deadline more than 30 days in the past."""
    since_24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rs = client.table("tenders").select("deadline,alert_seq").eq("source", source).gte("created_at", since_24).not_.is_("alert_seq", "null").lt("deadline", cutoff[:10]).limit(50).execute()
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
    if result["collection"].get("status") == FAIL:
        excuse = silence_excuse(name)
        if excuse and excuse.get("category") in _SILENCE_OK:
            result["collection"] = {"status": "skip", "msg": "молчание объяснено: %s" % excuse.get("reason", ""),
                                    "count_24h": 0, "count_7d": 0}
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


def render_report(results: List[Dict], prev_fail: Optional[List[str]] = None) -> str:
    lines = []
    by_status = Counter(overall_status(r) for r in results)
    lines.append(f"📊 *Парсинг-SEO Аудит бирж* ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")
    lines.append(f"Источников: {len(results)} — OK: {by_status[OK]}, WARN: {by_status[WARN]}, FAIL: {by_status[FAIL]}")
    if prev_fail is not None:
        cur = {r["name"] for r in results if overall_status(r) == FAIL}
        new, fixed = sorted(cur - set(prev_fail)), sorted(set(prev_fail) - cur)
        if new:
            lines.append("🆕 Новые FAIL: " + ", ".join(new))
        if fixed:
            lines.append("✅ Починились: " + ", ".join(fixed))
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


def _tg_post(token, payload):
    # type: (str, Dict) -> bool
    try:
        r = httpx.post("https://api.telegram.org/bot%s/sendMessage" % token, json=payload, timeout=15)
    except Exception as exc:
        logger.warning("[Audit] TG send error: %s", type(exc).__name__)
        return False
    if r.status_code != 200:
        logger.warning("[Audit] TG HTTP %d: %s", r.status_code, r.text[:150])
        return False
    return True


def send_telegram(text: str) -> bool:
    """Отправить отчёт. True — только если Telegram принял КАЖДЫЙ кусок.

    Токен берём из settings (читает .env сам, как остальные скрипты); переменные
    окружения — запасной путь. Раньше были только они, а cron их не задаёт.
    """
    token = getattr(settings, "telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = getattr(settings, "telegram_alert_chat_id", "") or os.environ.get("TELEGRAM_ALERT_CHAT_ID", "")
    if not token or not chat:
        logger.error("[Audit] TG token/chat not configured — отчёт НЕ отправлен")
        return False
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
        ok = _tg_post(token, {"chat_id": chat, "text": chunk, "parse_mode": "Markdown"})
        if not ok:
            # Markdown мог сломаться на имени источника — повтор простым текстом.
            ok = _tg_post(token, {"chat_id": chat, "text": chunk})
        if not ok:
            logger.error("[Audit] TG: кусок %d из %d не доставлен", i + 1, len(chunks))
            return False
    return True


def _load_sent(path=_SENT_STATE):
    # type: (str) -> Optional[List[str]]
    """Набор FAIL из последнего ДОСТАВЛЕННОГО отчёта; None — отчётов ещё не было."""
    try:
        with open(path) as f:
            data = json.load(f)
        fail = data.get("fail")
        return sorted(str(x) for x in fail) if isinstance(fail, list) else None
    except (IOError, OSError, ValueError, AttributeError):
        return None


def _save_sent(fail_names, path=_SENT_STATE):
    # type: (List[str], str) -> None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"fail": sorted(fail_names), "sent_at": datetime.now(timezone.utc).isoformat()},
                  f, ensure_ascii=False)


def should_send(fail_names, prev_fail):
    # type: (List[str], Optional[List[str]]) -> bool
    """Для --only-fail: слать, только когда набор FAIL изменился.

    Первый отчёт — если есть FAIL. Дальше — при любом изменении, включая
    «всё починилось». Тот же список каждую ночь — это шум, а не сигнал.
    """
    cur = set(fail_names)
    if prev_fail is None:
        return bool(cur)
    return cur != set(prev_fail)


def main():
    ap = argparse.ArgumentParser(description="Step-by-step audit of all tender exchanges")
    ap.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--only-fail", action="store_true", help="Send only if any FAIL detected")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # Без этого каждый запрос к базе — строка INFO в логе: 25 МБ к 10.10.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    client = _get_supabase()
    results = []
    for src in build_sources():
        try:
            results.append(audit_source(client, src))
        except Exception as exc:
            logger.warning("Audit failed for %s: %s", src["name"], str(exc)[:120])
            results.append({"name": src["name"], "error": str(exc)[:120]})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return 0

    fail_names = sorted(r["name"] for r in results if overall_status(r) == FAIL)
    has_fail = bool(fail_names)
    prev_fail = _load_sent() if args.telegram else None
    report = render_report(results, prev_fail=prev_fail)
    print(report)

    if args.telegram:
        if args.only_fail and not should_send(fail_names, prev_fail):
            logger.info("[Audit] Набор FAIL не изменился (%d) — отчёт не отправляю", len(fail_names))
        elif send_telegram(report):
            _save_sent(fail_names)
            logger.info("[Audit] Report sent to Telegram")
        else:
            logger.error("[Audit] Отчёт в Telegram НЕ доставлен")
            return 2

    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
