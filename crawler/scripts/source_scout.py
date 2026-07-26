"""source_scout — BIRTH-of-source symmetry to freshness_watchdog (which catches death).

Nyro SCOUT pattern: surface sources OUTSIDE the current map, cluster, PROPOSE — never
auto-connect. freshness_watchdog fires when a live source goes silent; this fires when a
new/relevant platform appears that we don't yet crawl.

What it does (all deterministic, VPS-safe — no Claude WebSearch on the box):
  1. SEED health-probe — GETs a curated list of UZ procurement platforms NOT in
     sources.yaml; a live page carrying procurement + print terms becomes a candidate.
  2. Migration-watch — probes covered hosts with a known migration risk (etender.uzex.uz
     → new-xarid) so a silent platform move is caught before the feed dies.
  3. Dedup vs sources.yaml hostnames (never re-propose something we already crawl).
Candidates land in crawler_settings['source_candidates'] (JSON) with a self-sufficient
note for Daniyar. 0 auto-connections — connecting a source stays a human decision.

Open-web discovery of brand-new platforms needs a search API the VPS lacks; that pass is
run in a Claude session and its findings are seeded below (2026-07-16 discovery).

Cron (host, weekly): 0 6 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.source_scout --scan
                     10 6 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.source_scout --report --tg
Usage: --scan (probe+store) | --report [--tg] | --dry-run (probe+print, no store/send)
"""
import argparse
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import yaml

from crawler.auth.session_store import session_store
from crawler.config.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("source-scout")


def _norm_host(h):
    # type: (str) -> str
    """Lowercase host with a 'www.' prefix stripped. NOT str.lstrip('www.') — that
    strips leading w/./ chars (worldbank.org -> orldbank.org)."""
    h = (h or "").lower()
    return h[4:] if h.startswith("www.") else h

CAND_KEY = "source_candidates"          # the proposal store (JSON list)
# Resolve relative to this file so it works both on the VPS and in a local checkout.
_SOURCES_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "sources.yaml")

# Relevance gate: a candidate page should read like procurement AND ideally printing.
_PROC_TERMS = ("tender", "xarid", "закуп", "лот", "auction", "аукцион", "procure", "e-tender")
_PRINT_TERMS = ("печат", "полиграф", "бумаг", "этикет", "упаков", "каталог", "буклет", "bosma", "qog'oz")

# ── Seed candidates (2026-07-16 in-session web discovery, cross-checked vs sources.yaml) ──
# kind: 'portal' = probe as a real candidate source; 'aggregator' = report-only note (low
# upside — re-lists platforms we already crawl directly).
# verdict: set once a candidate has been investigated by hand. A seed with a verdict is
# never probed or re-proposed again — the reasoning is kept here so the same dead end is
# not re-discovered in three months. Delete the verdict to reopen the candidate.
SEED = [
    {"name": "Mintrans e-tender portal", "url": "https://e-tender.uztrans.uz/", "kind": "portal",
     "why": "Портал э-тендеров Минтранса. У нас есть только TG @Mintrans_uz, не сам портал — прямой источник может нести лоты раньше/полнее.",
     "verdict": {"date": "2026-07-26", "outcome": "rejected",
                 "note": "Не закупки товаров: система раздаёт МАРШРУТЫ пассажирских перевозок "
                         "(автобус/такси) перевозчикам. У лота нет цены — есть номер маршрута и "
                         "тип авто. Печати/упаковки нет как класса. API e-tenderapi.uztrans.uz "
                         "/api/tender/list требует токен."}},
    {"name": "NIM open-data закупки", "url": "https://nim.uz/open-data/public-procurement/", "kind": "portal",
     "why": "Открытые данные по госзакупкам (машиночитаемо). Если есть API/дамп — дешёвый структурированный recall-слой.",
     "verdict": {"date": "2026-07-26", "outcome": "rejected",
                 "note": "nim.uz — сайт Института метрологии, не портал закупок. Страница "
                         "open-data/закупки пуста во всех трёх локалях: 0 таблиц, 0 файлов "
                         "(xlsx/csv/json/xml), только навигация. Выгрузки не существует."}},
    {"name": "TenderZone (augz.uz)", "url": "https://augz.uz/en/tenderzone/", "kind": "aggregator",
     "why": "E-IMZO аналитик-агрегатор поверх ETENDER.UZEX + XARID.UZEX — их мы уже краулим напрямую. Upside охвата НИЗКИЙ (не новый источник лотов, а перекладка). Регистрация ради этого малополезна."},
    {"name": "Bicotender (UZ раздел)", "url": "https://www.bicotender.ru/catalog/by-region/uzbekistan/", "kind": "aggregator",
     "why": "Российский агрегатор с UZ-разделом. Тоже re-list уже покрытых площадок. Report-only."},
]

# Covered hosts with a known migration risk (main.md): watch for a silent move.
MIGRATION_WATCH = [
    {"name": "etender.uzex.uz", "url": "https://etender.uzex.uz/", "risk": "миграция на new-xarid.uzex.uz"},
]


def _known_hosts():
    # type: () -> set
    """Hostnames (and @tg handles) we already crawl — the dedup target."""
    hosts = set()
    try:
        with open(_SOURCES_YAML, "r") as f:
            cfg = yaml.safe_load(f) or {}
        for s in cfg.get("sources", []):
            for k in ("url", "base_url", "domain"):
                v = s.get(k)
                if v and isinstance(v, str) and v.startswith("http"):
                    h = _norm_host(urlparse(v).hostname)
                    if h:
                        hosts.add(h)
            tg = s.get("telegram_channel")
            if tg:
                hosts.add(str(tg).lower().lstrip("@"))
    except Exception as exc:
        logger.warning("sources.yaml parse failed: %s", str(exc)[:100])
    return hosts


def _probe(url):
    # type: (str) -> dict
    """GET a URL; return {alive, status, relevant, has_print}. Fail-open (never raises)."""
    out = {"alive": False, "status": 0, "relevant": False, "has_print": False}
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; source-scout/1.0)"})
        out["status"] = r.status_code
        out["alive"] = r.status_code < 500
        txt = (r.text or "").lower()[:200000]
        out["relevant"] = any(t in txt for t in _PROC_TERMS)
        out["has_print"] = any(t in txt for t in _PRINT_TERMS)
        out["final_host"] = _norm_host(urlparse(str(r.url)).hostname)
    except Exception as exc:
        out["error"] = str(exc)[:80]
    return out


def _load_list(key):
    # type: (str) -> list
    """Read a stored list back out of its dict envelope.

    session_store.get_setting returns ONLY dicts — a bare list is dropped and comes
    back as None. This module stored bare lists from birth (18.07), so every Monday
    scan wrote 4 candidates and the report 10 minutes later read zero and announced
    "нет новых кандидатов (охват актуален)" to Telegram. Envelope, not bare list.
    """
    v = session_store.get_setting(key)
    if isinstance(v, dict):
        items = v.get("items")
        return items if isinstance(items, list) else []
    if isinstance(v, list):        # tolerate a hand-written legacy payload
        return v
    return []


def _load_candidates():
    # type: () -> list
    return _load_list(CAND_KEY)


def scan(dry):
    known = _known_hosts()
    logger.info("known hosts: %d", len(known))
    existing = {c["url"]: c for c in _load_candidates()}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    proposed = []

    for s in SEED:
        if s.get("verdict"):
            logger.info("skip %s — closed %s (%s)", s["name"],
                        s["verdict"].get("date"), s["verdict"].get("outcome"))
            continue
        host = _norm_host(urlparse(s["url"]).hostname)
        if host in known:
            logger.info("skip %s — already crawled (%s)", s["name"], host)
            continue
        p = _probe(s["url"]) if s["kind"] == "portal" else {"alive": True, "relevant": True, "has_print": False, "status": "n/a"}
        # decision tree: live+relevant → proposed; live+unclear → needs_probe; dead → skip
        if s["kind"] == "aggregator":
            status = "note-only"
        elif not p.get("alive"):
            logger.info("skip %s — dead (%s)", s["name"], p.get("error") or p.get("status"))
            continue
        elif p.get("relevant"):
            status = "proposed" if p.get("has_print") else "proposed-verify-scope"
        else:
            status = "needs_probe"
        rec = {
            "name": s["name"], "url": s["url"], "kind": s["kind"], "why": s["why"],
            "status": status, "http": p.get("status"), "has_print_terms": p.get("has_print", False),
            "first_seen": existing.get(s["url"], {}).get("first_seen", now), "last_scan": now,
        }
        proposed.append(rec)
        logger.info("candidate: %-28s [%s] http=%s print=%s", s["name"], status, p.get("status"), p.get("has_print"))

    # migration watch
    migr = []
    for m in MIGRATION_WATCH:
        p = _probe(m["url"])
        want_host = _norm_host(urlparse(m["url"]).hostname)
        moved = bool(p.get("final_host")) and want_host not in (p.get("final_host") or "")
        if moved or not p.get("alive"):
            migr.append({"name": m["name"], "risk": m["risk"], "final_host": p.get("final_host"),
                         "alive": p.get("alive"), "last_scan": now})
            logger.warning("MIGRATION/DEATH signal: %s → %s (alive=%s)", m["name"], p.get("final_host"), p.get("alive"))

    n_closed = len([s for s in SEED if s.get("verdict")])
    summary = {"scanned": len(SEED), "candidates": len(proposed),
               "closed": n_closed, "migration_flags": len(migr)}
    print("scout scan:", summary)
    for r in proposed:
        print("  [%s] %s — %s" % (r["status"], r["name"], r["url"]))
    if migr:
        print("  MIGRATION:", migr)

    if dry:
        print(">>> DRY-RUN — nothing stored.")
        return summary
    # Dict envelope — a bare list is dropped on read (see _load_list).
    session_store.set_setting(CAND_KEY, {"items": proposed, "last_scan": now})
    session_store.set_setting("source_scout_migration", {"items": migr, "last_scan": now})
    stored = len(_load_candidates())
    if stored != len(proposed):
        logger.error("READ-BACK MISMATCH: stored %d candidates, read %d — the report will lie",
                     len(proposed), stored)
    print(">>> stored %d candidates + %d migration flags to crawler_settings (read-back: %d)"
          % (len(proposed), len(migr), stored))
    return summary


def _fmt_report():
    cands = _load_candidates()
    migr = _load_list("source_scout_migration")
    portals = [c for c in cands if c.get("kind") == "portal"]
    aggs = [c for c in cands if c.get("kind") == "aggregator"]
    lines = ["\U0001f9ed *SCOUT — источники-кандидаты*"]
    if portals:
        lines.append("\n*Предложения (нужен твой коннект, 0 автоподключений):*")
        for c in portals:
            flag = "✅" if c["status"].startswith("proposed") else "❓"
            lines.append("%s *%s* — %s\n  _%s_" % (flag, c["name"], c["url"], c["why"]))
    if aggs:
        names = ", ".join(c["name"] for c in aggs)
        lines.append("\n_E-IMZO/агрегаторы (%s): re-list уже покрытых площадок, upside низкий — регистрация малополезна._" % names)
    if migr:
        lines.append("\n⚠️ *Миграция площадок:* " + "; ".join("%s→%s" % (m["name"], m.get("final_host")) for m in migr))
    if not portals and not migr:
        lines.append("нет новых кандидатов (охват актуален)")
    closed = [s for s in SEED if s.get("verdict")]
    if closed:
        lines.append("\n_Закрыто разведкой (не подключаем): %s._" % "; ".join(
            "%s — %s" % (c["name"], c["verdict"]["note"].split(".")[0]) for c in closed))
    return "\n".join(lines)


def report(send):
    body = _fmt_report()
    print(body)
    if send:
        try:
            httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                       json={"chat_id": settings.telegram_alert_chat_id, "text": body, "parse_mode": "Markdown"},
                       timeout=10)
            print("[report] sent to TG")
        except Exception as exc:
            logger.warning("TG send failed: %s", str(exc)[:80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="probe seeds + store candidates")
    ap.add_argument("--report", action="store_true", help="print/send the candidate report")
    ap.add_argument("--tg", action="store_true", help="with --report: send to Telegram")
    ap.add_argument("--dry-run", action="store_true", help="with --scan: probe+print, no store")
    a = ap.parse_args()
    if a.scan or a.dry_run:
        scan(dry=a.dry_run)
    if a.report:
        report(send=a.tg)
    if not (a.scan or a.dry_run or a.report):
        ap.print_help()


if __name__ == "__main__":
    main()
