"""Shadow-mode filter expansion — the self-extension engine (2026-07-05).

Champion/challenger for the SEARCH filter. Candidate matchers (new keywords ru+uz,
TNVED code prefixes) run in SHADOW over recent NON-alerted lots: they find what the
production keyword filter MISSED, an AI judge estimates how many are actually
in-scope, and a weekly report proposes «promote? [tap]». Nothing is ever sent from
shadow — a candidate must PROVE its catch in the dark before it can go live, and a
promoted keyword still passes the normal AI gate. If Daniyar later marks its catches
«Не моё», the existing auto-mute rolls it back. This is how the system proposes its
own recall improvements safely — «tries to find more, like a real AI».

Flow:
  --scan                nightly: score every active candidate over 14d of missed lots
  --report              weekly: print/TG per-candidate table (catches, in-scope%, samples)
  --promote <cand_id>   graduate: keyword→settings.alert_keywords; tnved→settings.tnved_scope
  --add-keyword W / --add-tnved P   register a new candidate to shadow-test

State in crawler_settings: shadow_candidates_v1 (defs+stats). Log: logs/shadow_catches.jsonl.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import httpx

from crawler.config.settings import settings
from crawler.core.models import RawTender

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("shadow_search")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSONL = os.path.join(REPO_ROOT, "logs", "shadow_catches.jsonl")
STATE_KEY = "shadow_candidates_v1"
JUDGE_SAMPLE = 12          # AI-judged lots per candidate per scan (cost guard)
WINDOW_DAYS = 14

# Biddable sources (same set the recall audit trusts).
SOURCES = [
    "Cooperation.uz Лоты", "B2Biz.uz (Тендеры)", "Hayotbirja отбор",
    "Hayotbirja встречные аукционы", "Hayotbirja тендеры", "ETender UZEX",
    "ETender Обсуждения", "Xarid Конкурсы", "Tender.mc.uz (Минстрой)",
    "UZEX Предквалификации", "UZEX Обратные аукционы", "XT-Xarid встречные аукционы",
    "XT-Xarid тендеры", "XT-Xarid запросы предложений",
]

# ── Seed candidates: the two empirically-proven ones from the 2026-07-05 gold-map ──
#  (verified: uz-titled «Рўйхатга олиш журнали» + 77 TNVED-48/49 lots invisible to
#   the ru keyword filter). New candidates get added via --add-* or the weekly LLM step.
SEED = [
    {"id": "tnved-print", "type": "tnved", "source": "gold-map-2026-07-05",
     # print/paper/packaging codes; 4818 (hygiene paper) deliberately EXCLUDED.
     "value": ["4817", "4819", "4820", "4821", "4909", "4910", "4911", "4901", "4902"]},
    {"id": "uz-print-words", "type": "keyword", "source": "gold-map-2026-07-05",
     "value": ["jurnal", "журнал", "daftar", "дафтар", "guvohnoma", "гувоҳнома",
               "chop etish", "nashriyot", "нашриёт", "yorliq", "buklet"]},
]

FIELDS = ("external_id,title,organization,price,deadline,source,search_text,"
          "message_type,extra_info,alert_seq")


def _client():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _load_state(store):
    st = store.get_setting(STATE_KEY)
    if not isinstance(st, dict) or "candidates" not in st:
        st = {"candidates": SEED, "results": {}}
    return st


def _tnved_of(row):
    ei = row.get("extra_info") or {}
    return str(ei.get("tnved") or ei.get("code") or "")


def _matches(cand, row):
    if cand["type"] == "tnved":
        t = _tnved_of(row)
        return bool(t) and any(t.startswith(p) for p in cand["value"])
    text = ((row.get("title") or "") + " " + (row.get("search_text") or "")).lower()
    return any(w.lower() in text for w in cand["value"])


def _to_tender(r):
    # extra_info intentionally omitted — shadow only needs title/search_text/org for
    # matching + judging, and DB extra_info holds int/bool values that fail the
    # RawTender Dict[str,str] schema (2026-07-06). tnved is read from the raw row.
    return RawTender(
        id=r.get("external_id") or "x", external_id=r.get("external_id") or "",
        title=r.get("title") or "", organization=r.get("organization") or "",
        price=r.get("price"), deadline=r.get("deadline"), source=r.get("source") or "",
        search_text=r.get("search_text") or "", message_type=r.get("message_type") or "tender")


def _pull_missed(c):
    """Recent (14d) NON-alerted lots in biddable sources — what production skipped."""
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    # Cap at ~1500 most-recent per source — representative for candidate catch-rate
    # measurement without pulling 20k+ rows (scan must finish inside cron budget).
    rows = []
    for src in SOURCES:
        for off in (0, 750):
            d = (c.table("tenders").select(FIELDS).eq("source", src)
                 .is_("alert_seq", "null").gte("collected_at", since)
                 .order("collected_at", desc=True).range(off, off + 749).execute().data) or []
            rows.extend(d)
            if len(d) < 750:
                break
    return rows


async def _judge_inscope(tenders):
    """AI-judge (deepseek via OpenRouter, product-scope prompt) → count in-scope (score>=70)."""
    from crawler.core.notifier import _RELEVANCE_PROMPT, _extract_json_object
    key = settings.openrouter_api_key
    if not key or not tenders:
        return None, []
    fast = getattr(settings, "ai_relevance_model", None) or "deepseek/deepseek-v4-flash"

    async def _one(cl, t):
        prompt = _RELEVANCE_PROMPT.format(
            playbook="", source_context="", title=(t.title or "")[:200],
            organization=t.organization or "—", details=(t.search_text or "")[:400])
        try:
            r = await cl.post("https://openrouter.ai/api/v1/chat/completions",
                              headers={"Authorization": "Bearer %s" % key},
                              json={"model": fast, "messages": [{"role": "user", "content": prompt}],
                                    "max_tokens": 120, "temperature": 0, "reasoning": {"enabled": False}})
            obj = _extract_json_object((((r.json().get("choices") or [{}])[0]).get("message") or {}).get("content") or "") or {}
            return int(obj.get("score") or 0), (t.title or "")[:50]
        except Exception:
            return 0, ""

    async with httpx.AsyncClient(timeout=30) as cl:
        scored = await asyncio.gather(*[_one(cl, t) for t in tenders])
    judged = [title for sc, title in scored if sc >= 70]
    return len(judged), judged


async def scan():
    from crawler.auth.session_store import session_store
    c = _client()
    st = _load_state(session_store)
    cands = st.get("candidates") or []
    if not cands:
        logger.info("[Shadow] no candidates"); return 0
    rows = _pull_missed(c)
    logger.info("[Shadow] %d non-alerted lots in window", len(rows))
    # production keyword matcher — isolate TRUE new recall (production kw didn't match)
    from crawler.core.notifier import _get_keywords, _find_matching_keyword
    kws = _get_keywords()
    results = st.setdefault("results", {})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for cand in cands:
        caught = []
        for r in rows:
            if not _matches(cand, r):
                continue
            t = _to_tender(r)
            if _find_matching_keyword(t, kws):
                continue  # production keyword already covers it → not new recall
            caught.append(t)
        import random
        logger.info("[Shadow] %-16s matched %d new lots, judging %d...",
                    cand["id"], len(caught), min(JUDGE_SAMPLE, len(caught)))
        sample = random.sample(caught, min(JUDGE_SAMPLE, len(caught))) if caught else []
        in_scope, judged = await _judge_inscope(sample)
        rec = {"date": now, "candidate": cand["id"], "type": cand["type"],
               "new_catches": len(caught),
               "judged": len(sample), "in_scope": in_scope,
               "in_scope_pct": round(100 * in_scope / len(sample)) if sample else None,
               "sample": judged[:5]}
        results[cand["id"]] = rec
        logger.info("[Shadow] %-16s new=%d judged=%d in_scope=%s%%",
                    cand["id"], len(caught), len(sample),
                    rec["in_scope_pct"] if rec["in_scope_pct"] is not None else "?")
        try:
            os.makedirs(os.path.dirname(JSONL), exist_ok=True)
            with open(JSONL, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except IOError:
            pass
    session_store.set_setting(STATE_KEY, st)
    return 0


async def report(send_tg=False):
    from crawler.auth.session_store import session_store
    st = _load_state(session_store)
    results = st.get("results") or {}
    if not results:
        print("нет результатов shadow-scan (запусти --scan)"); return 0
    lines = ["🌱 *Shadow-поиск — кандидаты в фильтр*", ""]
    worth = []
    for cid, r in sorted(results.items(), key=lambda kv: -(kv[1].get("new_catches") or 0)):
        pct = r.get("in_scope_pct")
        flag = "✅" if (pct is not None and pct >= 60 and (r.get("new_catches") or 0) >= 3) else "·"
        lines.append("%s *%s* (%s): поймал бы %d новых, in-scope ~%s%%"
                     % (flag, cid, r.get("type"), r.get("new_catches") or 0,
                        pct if pct is not None else "?"))
        if r.get("sample"):
            lines.append("   напр.: " + "; ".join(r["sample"][:2]))
        if flag == "✅":
            worth.append(cid)
    if worth:
        lines.append("")
        lines.append("Промоутить: `python3 -m crawler.scripts.shadow_search --promote <id>`")
        lines.append("Кандидаты к промоушену: " + ", ".join(worth))
    text = "\n".join(lines)
    print(text)
    if send_tg and settings.telegram_bot_token:
        async with httpx.AsyncClient(timeout=15) as cl:
            await cl.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                          json={"chat_id": settings.telegram_alert_chat_id, "text": text,
                                "parse_mode": "Markdown", "disable_web_page_preview": True})
    return 0


def promote(cand_id):
    """Graduate a candidate to the live filter (keyword → alert_keywords setting)."""
    from crawler.auth.session_store import session_store
    st = _load_state(session_store)
    cand = next((c for c in st.get("candidates", []) if c["id"] == cand_id), None)
    if not cand:
        print("нет кандидата %s" % cand_id); return 1
    c = _client()
    if cand["type"] == "keyword":
        row = (c.table("crawler_settings").select("value").eq("key", "alert_keywords")
               .limit(1).execute().data or [{}])
        cur = (row[0].get("value") or "") if row else ""
        cur_set = {k.strip().lower() for k in cur.split(",") if k.strip()}
        added = [w for w in cand["value"] if w.lower() not in cur_set]
        newval = cur + ("," if cur and added else "") + ",".join(added)
        c.table("crawler_settings").upsert({"key": "alert_keywords", "value": newval},
                                           on_conflict="key").execute()
        print("promoted keyword-candidate %s: +%d words -> alert_keywords" % (cand_id, len(added)))
    elif cand["type"] == "tnved":
        row = (c.table("crawler_settings").select("value").eq("key", "tnved_scope")
               .limit(1).execute().data or [{}])
        cur = (row[0].get("value") or "") if row else ""
        cur_set = {p.strip() for p in cur.split(",") if p.strip()}
        merged = sorted(cur_set | set(cand["value"]))
        c.table("crawler_settings").upsert({"key": "tnved_scope", "value": ",".join(merged)},
                                           on_conflict="key").execute()
        print("promoted tnved-candidate %s: scope now %s "
              "(notifier consults tnved_scope as an extra match path)" % (cand_id, merged))
    # mark promoted in state
    cand["promoted"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    session_store.set_setting(STATE_KEY, st)
    return 0


def add_candidate(kind, value):
    from crawler.auth.session_store import session_store
    st = _load_state(session_store)
    cid = "%s-%s" % (kind, re.sub(r"\W+", "", value)[:12])
    st.setdefault("candidates", []).append(
        {"id": cid, "type": kind, "source": "manual", "value": [value]})
    session_store.set_setting(STATE_KEY, st)
    print("added candidate %s" % cid)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--tg", action="store_true", help="send report to Telegram")
    ap.add_argument("--promote")
    ap.add_argument("--add-keyword")
    ap.add_argument("--add-tnved")
    a = ap.parse_args()
    if a.promote:
        sys.exit(promote(a.promote))
    if a.add_keyword:
        sys.exit(add_candidate("keyword", a.add_keyword))
    if a.add_tnved:
        sys.exit(add_candidate("tnved", a.add_tnved))
    if a.scan:
        sys.exit(asyncio.run(scan()))
    if a.report:
        sys.exit(asyncio.run(report(send_tg=a.tg)))
    ap.error("one of --scan/--report/--promote/--add-keyword/--add-tnved required")
