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

  4. --discover (26.07) — open-web pass for platforms nobody seeded. Runs on the
     OpenRouter key already in .env via the `web` plugin, same pattern Daniyar chose for
     investigator on 02.07: reuse the funded key, do not add a credential. ~$0.005 per
     weekly call. A model claim is NOT evidence: every proposal is filtered to .uz,
     deduped against sources.yaml + SEED, then probed like any seed before it is stored.

Cron (host, weekly): 0 6 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.source_scout --scan
                     5 6 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.source_scout --discover
                    10 6 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.source_scout --report --tg
Usage: --scan (probe+store) | --discover (web pass) | --report [--tg] | --dry-run
"""
import argparse
import json
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

# ── Open-web discovery (--discover) ───────────────────────────────────────────
# Same key and model as investigator.py — Daniyar 02.07: reuse the funded OpenRouter
# credential instead of adding an Anthropic/search one. deepseek-v4-* is a reasoning
# model: reasoning MUST stay disabled or it eats the budget and returns empty content
# (error-log 06-29).
DISCOVER_MODEL = "deepseek/deepseek-v4-pro"
DISCOVER_META_KEY = "source_discover_v1"   # last run / cost / counts — so a dead pass is visible
MAX_DISCOVER = 8
DISCOVER_STALE_DAYS = 10

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


def _today():
    # type: () -> str
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
    stored = _load_candidates()
    existing = {c["url"]: c for c in stored}
    now = _today()
    # scan() rebuilds its proposals from SEED and overwrites the store — web-discovery
    # findings are not in SEED, so they must be carried across or Monday's scan erases
    # what Monday's discover pass just found.
    proposed = [c for c in stored if c.get("kind") == "discovered"]

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
        # .get: carried-over entries come from the store, not from this run's SEED loop,
        # and a stored shape from an older version must not crash the whole scan.
        print("  [%s] %s — %s" % (r.get("status", "?"), r.get("name", "?"), r.get("url", "?")))
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


def _parse_json_array(text):
    # type: (str) -> list
    """Pull the JSON array out of a reply that may wrap it in prose or code fences."""
    if not text:
        return []
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        out = json.loads(text[i:j + 1])
    except ValueError:
        return []
    return out if isinstance(out, list) else []


def _discover_prompt(domains):
    # type: (list) -> str
    """Known platforms go in as ANTI-context. A naive 'find UZ procurement sites' query
    came back with Kazakh portals, a Russian service and a trade magazine (probe 26.07);
    the same query with the known list and an explicit empty-answer escape hatch came
    back with [] and an explanation. Asking for what we DON'T have is the whole trick."""
    return (
        "Задача: перечислить ЭЛЕКТРОННЫЕ ПЛОЩАДКИ ЗАКУПОК УЗБЕКИСТАНА (только домены .uz, "
        "только сайты, где реально публикуются лоты/тендеры/аукционы и можно подать заявку).\n\n"
        "УЖЕ ИЗВЕСТНЫ, НЕ ПОВТОРЯТЬ: %s\n\n"
        "Нужны ДРУГИЕ площадки, которых нет в списке выше. Не предлагать: новостные сайты, "
        "отраслевые журналы, агрегаторы-перепродажи, площадки других стран (.kz, .ru), "
        "сайты одной организации со своими объявлениями.\n"
        "Если других площадок нет — верни пустой массив []. Пустой ответ лучше выдуманного.\n"
        'Ответ: ТОЛЬКО JSON-массив {"name":..., "url":..., "why":...}, максимум %d.'
        % (", ".join(domains[:40]), MAX_DISCOVER)
    )


def discover(dry):
    # type: (bool) -> dict
    """One bounded web-search pass; survivors are merged into the candidate store."""
    known = _known_hosts()
    seed_hosts = set()
    for s in SEED:
        h = _norm_host(urlparse(s["url"]).hostname)
        if h:
            seed_hosts.add(h)
    domains = sorted([h for h in known if "." in h])   # drop @tg handles

    api_key = settings.openrouter_api_key
    if not api_key:
        logger.error("no OpenRouter key — discovery skipped")
        return {"error": "no key"}

    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer %s" % api_key},
            json={"model": DISCOVER_MODEL,
                  "plugins": [{"id": "web", "engine": "parallel", "max_results": 8}],
                  "messages": [{"role": "user", "content": _discover_prompt(domains)}],
                  "max_tokens": 1200, "temperature": 0,
                  "reasoning": {"enabled": False},     # reasoning model — see header
                  "usage": {"include": True}},
            timeout=180)
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        logger.error("discovery call failed: %s", str(exc)[:160])
        return {"error": str(exc)[:160]}

    msg = (body.get("choices") or [{}])[0].get("message") or {}
    raw = _parse_json_array(msg.get("content") or "")
    cost = float((body.get("usage") or {}).get("cost") or 0.0)
    logger.info("discovery: model returned %d item(s), %d web sources, cost $%.4f",
                len(raw), len(msg.get("annotations") or []), cost)

    # A model claim is not evidence: filter to .uz, dedup, then probe like any seed.
    fresh, rejected = [], []
    seen = set()
    for item in raw[:MAX_DISCOVER]:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        host = _norm_host(urlparse(url if url.startswith("http") else "https://" + url).hostname)
        if not host or not host.endswith(".uz"):
            rejected.append("%s (не .uz)" % (host or url[:40]))
            continue
        if host in known or host in seed_hosts or host in seen:
            rejected.append("%s (уже знаем)" % host)
            continue
        seen.add(host)
        p = _probe(url)
        if not p.get("alive") or not p.get("relevant"):
            rejected.append("%s (проба: alive=%s relevant=%s)" % (host, p.get("alive"), p.get("relevant")))
            continue
        fresh.append({
            "name": (item.get("name") or host)[:80], "url": url, "kind": "discovered",
            "why": (item.get("why") or "")[:400],
            "status": "proposed" if p.get("has_print") else "proposed-verify-scope",
            "http": p.get("status"), "has_print_terms": p.get("has_print", False),
            "found_by": "web-discovery", "first_seen": _today(), "last_scan": _today(),
        })

    for x in rejected:
        logger.info("discovery rejected: %s", x)
    for c in fresh:
        logger.warning("DISCOVERY HIT: %s — %s", c["name"], c["url"])

    print("discovery: %d returned, %d kept, %d rejected, cost $%.4f"
          % (len(raw), len(fresh), len(rejected), cost))
    if dry:
        print(">>> DRY-RUN — nothing stored.")
        return {"returned": len(raw), "kept": len(fresh), "cost": cost}

    # Merge: keep every existing candidate, add only genuinely new hosts.
    current = _load_candidates()
    have = set()
    for c in current:
        h = _norm_host(urlparse(c.get("url") or "").hostname)
        if h:
            have.add(h)
    added = [c for c in fresh if _norm_host(urlparse(c["url"]).hostname) not in have]
    session_store.set_setting(CAND_KEY, {"items": current + added, "last_scan": _today()})

    meta = session_store.get_setting(DISCOVER_META_KEY) or {}
    session_store.set_setting(DISCOVER_META_KEY, {
        "last_run": _today(),
        "runs": int(meta.get("runs") or 0) + 1,
        "returned": len(raw), "kept": len(added), "rejected": len(rejected),
        "cost_usd_total": round(float(meta.get("cost_usd_total") or 0.0) + cost, 4),
    })
    print(">>> merged %d new candidate(s) into the store" % len(added))
    return {"returned": len(raw), "kept": len(added), "cost": cost}


def _clip(text, limit=150):
    # type: (str, int) -> str
    """Trim to a word boundary. NOT text.split('.')[0] — that cut the NIM verdict at
    'nim' because the note opens with the hostname 'nim.uz'."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _discover_status():
    # type: () -> str
    """One line on the web pass itself. A discovery that quietly stopped running looks
    exactly like a discovery that found nothing — the report must tell them apart."""
    meta = session_store.get_setting(DISCOVER_META_KEY) or {}
    last = meta.get("last_run")
    if not last:
        return "\n_Веб-разведка: ещё ни разу не запускалась._"
    try:
        age = (datetime.now(timezone.utc).date()
               - datetime.strptime(last, "%Y-%m-%d").date()).days
    except ValueError:
        age = -1
    line = ("\n_Веб-разведка: %s (%d прогонов, потрачено $%s). Последний: вернула %s, "
            "оставила %s._" % (last, meta.get("runs") or 0, meta.get("cost_usd_total") or 0,
                               meta.get("returned"), meta.get("kept")))
    if age > DISCOVER_STALE_DAYS or age < 0:
        line += "\n\U0001f7e5 *Разведка не запускалась %s дней — проверить крон.*" % age
    return line


def _fmt_report():
    cands = _load_candidates()
    migr = _load_list("source_scout_migration")
    portals = [c for c in cands if c.get("kind") == "portal"]
    aggs = [c for c in cands if c.get("kind") == "aggregator"]
    lines = ["\U0001f9ed *SCOUT — источники-кандидаты*"]
    if portals:
        lines.append("\n*Предложения (нужен твой коннект, 0 автоподключений):*")
        for c in portals:
            flag = "✅" if (c.get("status") or "").startswith("proposed") else "❓"
            lines.append("%s *%s* — %s\n  _%s_" % (flag, c["name"], c["url"], c["why"]))
    if aggs:
        names = ", ".join(c["name"] for c in aggs)
        lines.append("\n_E-IMZO/агрегаторы (%s): re-list уже покрытых площадок, upside низкий — регистрация малополезна._" % names)
    if migr:
        lines.append("\n⚠️ *Миграция площадок:* " + "; ".join("%s→%s" % (m["name"], m.get("final_host")) for m in migr))
    found = [c for c in cands if c.get("kind") == "discovered"]
    if found:
        lines.append("\n*Найдено веб-разведкой (проверено пробой, не подключено):*")
        for c in found:
            lines.append("🆕 *%s* — %s\n  _%s_" % (c["name"], c["url"], _clip(c.get("why") or "")))
    if not portals and not found and not migr:
        lines.append("нет новых портал-кандидатов (охват актуален)")
    lines.append(_discover_status())
    closed = [s for s in SEED if s.get("verdict")]
    if closed:
        lines.append("\n_Закрыто разведкой (не подключаем):_")
        for c in closed:
            lines.append("• *%s* — %s" % (c["name"], _clip(c["verdict"]["note"])))
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
    ap.add_argument("--discover", action="store_true",
                    help="open-web pass via OpenRouter web plugin (~$0.005/run)")
    ap.add_argument("--dry-run", action="store_true", help="probe+print, no store")
    a = ap.parse_args()
    if a.discover:
        discover(dry=a.dry_run)
    elif a.scan or a.dry_run:
        scan(dry=a.dry_run)
    if a.report:
        report(send=a.tg)
    if not (a.scan or a.dry_run or a.report or a.discover):
        ap.print_help()


if __name__ == "__main__":
    main()
