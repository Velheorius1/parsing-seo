"""customer_audit — «ловим ли мы тендеры вот этого заказчика, и ловили бы сейчас?»

Answers a question the operational metrics cannot (Daniyar 27.07, banks as the
first run): take a named buyer, find every print-adjacent tender it ever put
out, and split the funnel into the three places we can lose it —

    на площадке  →  собрали в БД  →  алертили тогда  →  поймал бы СЕГОДНЯШНИЙ стек

Layer 1 (platform vs our DB) exposes collection holes: rows the crawler never
saw. Layer 2 (alerted then) is history. Layer 3 replays each candidate through
the current filter stack (crawler.scripts.replay) — that is the actual «после
всех изменений не стало ли хуже» check, per tender, with the killing stage
named.

Generic by design: banks are just the first --name-patterns. Read-only — no DB
writes, no Telegram alerts (only the optional report to the alert channel).

Usage:
  python3 -m crawler.scripts.customer_audit --name-patterns "xalq bank,халк банк" \\
      [--inns 207215726] [--sweep-platforms] [--deep] [--ai] [--tg] \\
      [--export-corpus logs/corpus_candidates.json] [--max-pages N]
"""
import os

os.environ.setdefault("PARSING_AI_LOG", "/tmp/replay-ai-decisions.jsonl")

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/opt/parsing-seo")

import httpx  # noqa: E402

CACHE_DIR = "/tmp/customer_audit_cache"

# etender civil contracts: small enough to sweep whole (7.9k + 0.4k rows), and
# customer_Inn IS honored server-side (positive control 2026-07-27: filtering by
# a known INN returned only that INN's rows; a fake INN returned none).
_ETENDER = "https://apietender.uzex.uz/api/CivilContracts/%s"
_ETENDER_EPS = ("GetResulted", "GetNotResulted")
# Big client-side sweeps (opt-in via --deep): no server-side name/INN filter, but
# every row carries customer_name + customer_inn.
_DEALS = "https://apietender.uzex.uz/api/common/DealsList"
_DIRECT = "https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases"

# Sources where the customer name actually lands in a field we can read.
# Deliberately NOT an ILIKE query: organization is unindexed and ILIKE on the
# big sources returns 57014 every time (verified). We page and filter in Python.
_SCAN_SOURCES = [
    "ETender UZEX", "ETender Обсуждения", "ETender Несостоявшиеся (лиды)",
    "ETender Сделки (победители)", "UZEX Предквалификации", "Xarid Прямые закупки",
    "UZEX Результаты", "Узпромстройбанк (SQB)", "Sanoat Qurilish Bank",
    "Tender.mc.uz (Минстрой)", "XT-Xarid тендеры", "XT-Xarid встречные аукционы",
    "Hayotbirja отбор", "Cooperation.uz Контракты", "Cooperation.uz Аукционы",
]

_FIELDS = ("external_id,source,title,organization,search_text,price,currency,deadline,"
           "message_type,extra_info,bid_count,status,collected_at,alert_seq,"
           "relevance_score,relevance_category")

# Legal-form noise that must not defeat a name match.
_LEGAL_NOISE = re.compile(
    r"\b(акб|акционерно[а-я]*|коммерческ[а-я]*|банк[аиу]?|aksiyadorlik|tijorat|"
    r"at|ат|ооо|оао|ао|чп|мчж|ак)\b", re.I)


def _norm(s):
    # type: (str) -> str
    """Casefold + strip quotes/legal noise so «АКБ „Узпромстройбанк"» matches
    «Узпромстройбанк» and «AKSIYADORLIK TIJORAT XALQ BANKI» matches «xalq bank»."""
    s = (s or "").casefold().replace("ʻ", "'").replace("`", "'")
    s = re.sub(r"[«»\"'()]", " ", s)
    return " ".join(s.split())


def _matches(text, patterns):
    # type: (str, list) -> bool
    n = _norm(text)
    if any(p in n for p in patterns):
        return True
    stripped = " ".join(_LEGAL_NOISE.sub(" ", n).split())
    return any(p in stripped for p in patterns)


def _cache_path(tag):
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    return os.path.join(CACHE_DIR, "%s.json" % tag)


# ── Phase A/B: platforms ─────────────────────────────────────────────────────

def _sweep_etender(patterns, inns, http):
    """Full sweep of the two civil-contract endpoints + INN-targeted queries."""
    found = []
    for ep in _ETENDER_EPS:
        cache = _cache_path("etender_%s" % ep)
        if os.path.exists(cache):
            rows = json.load(open(cache))
        else:
            # from/to are INDEX BOUNDS, not offset+limit (probed 2026-07-27:
            # {from:0,to:500} and {from:500,to:1000} return adjacent windows with
            # a 1-row overlap). Passing a constant `to` silently returns page 1
            # forever — the first sweep took 501 of 7943 rows and looked fine.
            rows, seen, offset, step = [], set(), 0, 500
            while True:
                page = http.post(_ETENDER % ep,
                                 json={"from": offset, "to": offset + step}).json()
                if not isinstance(page, list) or not page:
                    break
                for r in page:
                    key = r.get("civil_contract_id") or r.get("deal_num") or id(r)
                    if key not in seen:
                        seen.add(key)
                        rows.append(r)
                total = page[0].get("total_count") or 0
                offset += step
                if len(page) < step or (total and offset >= total):
                    break
            json.dump(rows, open(cache, "w"), ensure_ascii=False)
        print("  %s: %d rows swept" % (ep, len(rows)))
        for r in rows:
            if _matches(r.get("customer_name") or "", patterns):
                found.append(("etender:%s" % ep, r))
        # INN-targeted (catches branches whose NAME differs but INN is known)
        for inn in inns:
            hits = http.post(_ETENDER % ep, json={"from": 0, "to": 500,
                                                  "customer_Inn": str(inn)}).json()
            if isinstance(hits, list):
                for r in hits:
                    found.append(("etender:%s:inn" % ep, r))
    return found


def _sweep_big(url, body_fn, tag, patterns, http, max_pages, page=500):
    """Client-side sweep of a large endpoint with page cache + resume."""
    found, offset, pages = [], 0, 0
    while pages < max_pages:
        cache = _cache_path("%s_%d" % (tag, offset))
        if os.path.exists(cache):
            rows = json.load(open(cache))
        else:
            try:
                # body_fn gets (from_index, to_index) — these endpoints bound by
                # index, not offset+limit (see _sweep_etender).
                rows = http.post(url, json=body_fn(offset, offset + page)).json()
            except Exception as exc:
                print("  %s @%d failed: %s" % (tag, offset, str(exc)[:70]))
                break
            if not isinstance(rows, list):
                break
            json.dump(rows, open(cache, "w"), ensure_ascii=False)
        if not rows:
            break
        for r in rows:
            if _matches(r.get("customer_name") or "", patterns):
                found.append((tag, r))
        total = (rows[0].get("total_count") or 0) if rows else 0
        offset += page
        pages += 1
        if len(rows) < page or (total and offset >= total):
            break
    print("  %s: %d pages, %d matched (total seen %d)" % (tag, pages, len(found), offset))
    return found


def resolve_inns(platform_hits, db_rows, patterns):
    """Bank branches hold their OWN INNs — collect the SET, not one number."""
    inns = defaultdict(lambda: {"names": set(), "hits": 0})
    for _tag, r in platform_hits:
        inn, name = r.get("customer_inn"), r.get("customer_name") or ""
        if inn and _matches(name, patterns):
            inns[str(inn)]["names"].add(name[:60])
            inns[str(inn)]["hits"] += 1
    for r in db_rows:
        ei = r.get("extra_info") or {}
        inn = ei.get("customer_inn") or ei.get("ИНН заказчика")
        if inn:
            inns[str(inn)]["names"].add((r.get("organization") or "")[:60])
            inns[str(inn)]["hits"] += 1
    return inns


# ── Phase C: our DB ──────────────────────────────────────────────────────────

def scan_our_db(patterns, sources=None):
    from crawler.core.db import iter_rows

    hits = []
    for src in (sources or _SCAN_SOURCES):
        n = 0
        try:
            for page in iter_rows("tenders", _FIELDS,
                                  filters=[("eq", ("source", src))],
                                  page_size=1000, label="audit:%s" % src[:16],
                                  max_pages=60):
                n += len(page)
                for r in page:
                    blob = "%s %s" % (r.get("organization") or "", r.get("search_text") or "")
                    if _matches(blob, patterns):
                        hits.append(r)
        except Exception as exc:
            print("  %-34s SCAN FAILED: %s" % (src, str(exc)[:60]))
            continue
        print("  %-34s %6d rows scanned" % (src, n))
    return hits


# ── Phase D: print-topic prescreen ───────────────────────────────────────────

def prescreen(rows):
    """kw_hit = the production keyword matcher; wide_hit = the broad recall regex
    from recall_audit. wide-without-kw is the interesting set: print-looking
    tenders our keyword list cannot see."""
    from crawler.core.notifier import _get_keywords, _find_matching_keyword
    from crawler.scripts.recall_audit import STRONG
    from crawler.scripts.replay import row_to_raw_tender

    # STRONG is an alternation SOURCE string in recall_audit, not a compiled
    # pattern — imported (not copied) so the two stay in sync by construction.
    wide_re = re.compile(STRONG, re.I)
    kws = _get_keywords()
    out = []
    for r in rows:
        t = row_to_raw_tender(r)
        kw = _find_matching_keyword(t, kws)
        blob = "%s %s" % (r.get("title") or "", r.get("search_text") or "")
        wide = bool(wide_re.search(blob))
        if kw or wide:
            out.append({"row": r, "tender": t, "kw_hit": kw, "wide_hit": wide})
    return out


# ── Report ───────────────────────────────────────────────────────────────────

def _fmt_report(patterns, inns, platform_hits, db_hits, cands, verdicts, deep):
    L = []
    L.append("\U0001f3e6 *Аудит заказчика* — %s" % ", ".join(patterns[:4]))
    L.append("")
    L.append("*Воронка*")
    L.append("```")
    if platform_hits:
        L.append("на площадках (свип)   : %d" % len(platform_hits))
    L.append("в нашей БД            : %d" % len(db_hits))
    uniq = len(set((c["row"].get("external_id"), c["row"].get("source")) for c in cands))
    L.append("из них печатных       : %d%s" % (
        len(cands), "" if uniq == len(cands) else " (уникальных %d)" % uniq))
    alerted = len([c for c in cands if (c["row"].get("alert_seq") is not None)])
    L.append("алертилось тогда      : %d" % alerted)
    if verdicts:
        passed = len([v for v in verdicts if v.passed_prefilter])
        delivered = len([v for v in verdicts if v.delivered])
        push = len([v for v in verdicts if v.route == "push"])
        L.append("прошло бы префильтр   : %d" % passed)
        L.append("дошло бы до отправки  : %d  (push %d / digest %d)"
                 % (delivered, push, delivered - push))
    L.append("```")

    if inns:
        top = sorted(inns.items(), key=lambda kv: -kv[1]["hits"])[:6]
        L.append("*ИНН найдено:* %d — %s" % (
            len(inns), ", ".join("%s (%d)" % (i, d["hits"]) for i, d in top)))

    if verdicts:
        delivered_n = len([v for v in verdicts if v.delivered])
        if delivered_n != alerted:
            L.append("*Сейчас против тогда:* %d → %d %s" % (
                alerted, delivered_n,
                "(стало лучше)" if delivered_n > alerted else "(стало ХУЖЕ)"))

        misses = [v for v in verdicts if not v.delivered]
        if misses:
            # Price context matters: "8 потеряно на цене" reads alarming until you
            # see they are sub-1.5M dust. Show the band instead of the bare count.
            price_of = {}
            for c in cands:
                price_of[c["tender"].external_id] = c["row"].get("price")
            stages = Counter(v.dropped_at_stage or "ai-reject" for v in misses)
            L.append("")
            L.append("*Где теряем сейчас* (%d шт)" % len(misses))
            L.append("```")
            for st, n in stages.most_common():
                prices = [price_of.get(v.external_id) for v in misses
                          if (v.dropped_at_stage or "ai-reject") == st]
                prices = [p for p in prices if p]
                band = ""
                if prices:
                    band = "  %.1f-%.1fM" % (min(prices) / 1e6, max(prices) / 1e6)
                L.append("%-20s %2d%s" % (st, n, band))
            L.append("```")
            for v in misses[:4]:
                L.append("• `%s` — %s" % (v.dropped_at_stage or "ai", (v.title or "")[:56]))

    wide_only = [c for c in cands if c["wide_hit"] and not c["kw_hit"]]
    if wide_only:
        L.append("")
        L.append("⚠️ *Печатные по смыслу, но мимо ключевиков:* %d" % len(wide_only))
        for c in wide_only[:4]:
            L.append("• %s" % (c["row"].get("title") or "")[:60])

    L.append("")
    L.append("_Replay идёт по СОХРАНЁННОМУ тексту строки — это «текущий стек на "
             "сегодняшних данных», не машина времени._")
    if not deep:
        L.append("_Свип больших площадок (Прямые закупки, Сделки) не запускался: --deep._")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name-patterns", required=True, help="через запятую")
    ap.add_argument("--inns", default="", help="известные ИНН через запятую")
    ap.add_argument("--sweep-platforms", action="store_true", help="свип etender (дёшево)")
    ap.add_argument("--deep", action="store_true", help="+ свип больших площадок")
    ap.add_argument("--max-pages", type=int, default=20)
    ap.add_argument("--ai", action="store_true", help="AI-гейт в replay (стоит центы)")
    ap.add_argument("--tg", action="store_true")
    ap.add_argument("--export-corpus")
    a = ap.parse_args()

    patterns = [_norm(p) for p in a.name_patterns.split(",") if p.strip()]
    inns_in = [x.strip() for x in a.inns.split(",") if x.strip()]
    print("patterns:", patterns)

    platform_hits = []
    if a.sweep_platforms or a.deep:
        print("\n=== A/B: площадки ===")
        with httpx.Client(timeout=60, headers={"User-Agent": "Mozilla/5.0",
                                               "Content-Type": "application/json"}) as h:
            platform_hits += _sweep_etender(patterns, inns_in, h)
            if a.deep:
                platform_hits += _sweep_big(
                    _DEALS, lambda o, p: {"System_Id": 0, "From": o, "To": p},
                    "deals", patterns, h, a.max_pages)
                platform_hits += _sweep_big(
                    _DIRECT, lambda o, p: {"from": o, "to": p},
                    "direct", patterns, h, a.max_pages)
        print("  platform hits: %d" % len(platform_hits))

    print("\n=== C: наша БД (постранично, без ILIKE) ===")
    db_hits = scan_our_db(patterns)
    print("  matched rows: %d" % len(db_hits))

    print("\n=== D: прескрин печатной тематики ===")
    cands = prescreen(db_hits)
    print("  print-adjacent: %d (kw %d / wide-only %d)" % (
        len(cands), len([c for c in cands if c["kw_hit"]]),
        len([c for c in cands if c["wide_hit"] and not c["kw_hit"]])))

    print("\n=== E: replay текущим стеком ===")
    verdicts = []
    if cands:
        from crawler.scripts.replay import replay_tenders
        ts = dict((c["tender"].external_id, c["row"].get("collected_at")) for c in cands)
        verdicts = asyncio.run(replay_tenders(
            [c["tender"] for c in cands], use_ai=a.ai,
            as_of="collected_at", collected_at=ts))
        for v in verdicts[:40]:
            print("  %-9s | ai=%-12s | route=%-7s | %s" % (
                v.dropped_at_stage or "passed",
                "skipped" if v.ai_skipped else "%s/%s" % (v.ai_score, v.ai_category),
                v.route, (v.title or "")[:52]))

    inns = resolve_inns(platform_hits, db_hits, patterns)
    report = _fmt_report(patterns, inns, platform_hits, db_hits, cands, verdicts, a.deep)
    print("\n" + report)

    if a.export_corpus:
        # zip positionally: replay_tenders returns 1:1 in input order. Keying by
        # external_id collapsed rows that repeat across id-spaces and mislabeled
        # their history (caught on the first bank run).
        out = []
        for c, v in zip(cands, verdicts):
            r = c["row"]
            out.append({
                "external_id": r.get("external_id"), "source": r.get("source"),
                "title": r.get("title"), "organization": r.get("organization"),
                "search_text": r.get("search_text"), "price": r.get("price"),
                "deadline": r.get("deadline"), "extra_info": r.get("extra_info") or {},
                "message_type": r.get("message_type"), "collected_at": str(r.get("collected_at")),
                "alerted_then": r.get("alert_seq") is not None,
                "kw_hit": c["kw_hit"], "wide_hit": c["wide_hit"],
                "replay": {"dropped_at": v.dropped_at_stage, "delivered": v.delivered,
                           "route": v.route, "ai_score": v.ai_score},
                # proposed only — a human labels before anything enters the corpus
                "proposed_label": "relevant" if (c["kw_hit"] and v.delivered) else "unknown",
            })
        json.dump(out, open(a.export_corpus, "w"), ensure_ascii=False, indent=1)
        print("\n>>> corpus candidates → %s (%d)" % (a.export_corpus, len(out)))

    if a.tg:
        from crawler.config.settings import settings
        try:
            with httpx.Client(timeout=20, trust_env=False) as c:
                resp = c.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                              json={"chat_id": settings.telegram_alert_chat_id, "text": report,
                                    "parse_mode": "Markdown", "disable_web_page_preview": True})
            print("[TG]", resp.status_code)
        except Exception as exc:
            print("[TG] failed:", str(exc)[:120])


if __name__ == "__main__":
    main()
