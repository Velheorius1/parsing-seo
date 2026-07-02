"""Investigator agent (V4, design 2026-07-02) — the ONE true agent in the stack.

Per Anthropic «Building effective agents»: built DIRECTLY on the Anthropic API
(no framework), bounded agentic loop (max 10 turns, 5-min budget), tools are thin
wrappers over the deterministic infrastructure (verifier / get_proc / DB).

For a contested/high-value lot it autonomously: pulls full platform detail
(get_proc purchase_positions, GetTrade), cross-checks the same lot across
platforms in our DB, weighs volumes/deadline/competition — and returns a
STRUCTURED verdict: участвовать / пропустить / уточнить + почему + что
подготовить + риски. Delivered as a Telegram REPLY to the original alert.

Triggers:
  --seq N     investigate alert #N (manual)
  --auto      scan recent pushed alerts: price >= 100M, not yet investigated,
              cap 10/day (counter in crawler_settings) — cron-able.

Budget guards (Anthropic anti-pattern: unbounded loops):
  max 10 model turns, 10 investigations/day, cost logged per run to
  crawler_settings 'investigations_v1'. Model: claude-sonnet-5 (per research:
  Sonnet for judgment; Haiku too weak for procurement docs, Opus overkill).

Requires ANTHROPIC_API_KEY in /opt/parsing-seo/.env — exits gracefully if absent.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import httpx

from crawler.config.settings import settings
from crawler.core.models import RawTender

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("investigator")

MODEL = "claude-sonnet-5"
MAX_TURNS = 10
DAILY_CAP = 10
STATE_KEY = "investigations_v1"
PRICE_TRIGGER = 100_000_000

SYSTEM = """Ты — тендерный аналитик типографии Winch Group (Ташкент; печать, полиграфия,
упаковка, стенды/таблички/бейджи, печать на мерче). Твоя задача — разобрать ОДИН лот и
дать владельцу чёткий вердикт: участвовать / пропустить / уточнить.

Используй инструменты чтобы: (1) получить полную деталь лота с площадки,
(2) проверить его текущий статус, (3) найти этот же лот на других площадках в нашей БД.
Затем выдай вердикт через submit_verdict. Учитывай: профиль типографии (широкоформат/
наружка/папки — НЕ наш профиль), объёмы и сроки, число конкурентов (part_count),
дедлайн, полноту данных. Если данных мало — вердикт «уточнить» с конкретным списком.
Не выдумывай факты: чего нет в данных — того не утверждай."""

TOOLS = [
    {"name": "fetch_lot_detail",
     "description": "Полная деталь лота с площадки (get_proc для birja: позиции закупки, документы, условия; GetTrade для etender).",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_lot_alive",
     "description": "Текущий статус лота на площадке: ok (активен) / closed / gone / unverifiable.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "find_cross_platform",
     "description": "Найти этот же лот на других площадках в нашей БД (по названию+организации).",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "submit_verdict",
     "description": "Финальный вердикт. ОБЯЗАТЕЛЬНО вызвать в конце ровно один раз.",
     "input_schema": {"type": "object", "properties": {
         "verdict": {"type": "string", "enum": ["участвовать", "пропустить", "уточнить"]},
         "why": {"type": "string", "description": "2-4 предложения обоснования"},
         "deadline_note": {"type": "string", "description": "дедлайн и сколько времени осталось"},
         "prepare": {"type": "array", "items": {"type": "string"}, "description": "что подготовить для участия (если участвовать/уточнить)"},
         "risks": {"type": "array", "items": {"type": "string"}, "description": "главные риски (до 3)"}},
         "required": ["verdict", "why"]}},
]


def _row_to_tender(r):
    return RawTender(
        id=r.get("id") or r.get("external_id"), external_id=r.get("external_id") or "",
        title=r.get("title") or "", organization=r.get("organization") or "",
        price=r.get("price"), currency=r.get("currency") or "UZS",
        deadline=r.get("deadline"), source=r.get("source") or "",
        source_url=r.get("source_url") or "", search_text=r.get("search_text") or "",
        message_type=r.get("message_type") or "tender",
        extra_info=r.get("extra_info") if isinstance(r.get("extra_info"), dict) else {},
    )


async def _tool_fetch_detail(t):
    from crawler.core.verifier import _birja_base
    base = _birja_base(t.source)
    if base:
        try:
            async with httpx.AsyncClient(timeout=15) as cl:
                r = await cl.post(base + "/urpc", json={
                    "jsonrpc": "2.0", "id": 1, "method": "get_proc",
                    "params": {"proc_id": int(t.external_id)}})
            res = r.json().get("result")
            if isinstance(res, dict):
                return json.dumps(res, ensure_ascii=False, default=str)[:6000]
        except Exception as exc:
            return "detail fetch failed: %s" % str(exc)[:100]
    if t.source.startswith("ETender"):
        import re as _re
        m = _re.search(r"/lot/(\d+)", t.source_url or "")
        if m:
            try:
                async with httpx.AsyncClient(timeout=15) as cl:
                    r = await cl.get("https://apietender.uzex.uz/api/common/GetTrade/%s/0" % m.group(1))
                return json.dumps(r.json(), ensure_ascii=False, default=str)[:6000]
            except Exception as exc:
                return "detail fetch failed: %s" % str(exc)[:100]
    return "no detail API for source %s; use the alert fields" % t.source


async def _tool_check_alive(t):
    from crawler.core.verifier import verify_lot
    async with httpx.AsyncClient(timeout=10) as cl:
        r = await verify_lot(t, cl)
    return "%s (%s)" % (r.status, r.reason)


def _tool_cross_platform(t, client):
    from crawler.core.dedup import _extract_significant_words
    words = _extract_significant_words(t.title or "")
    if not words:
        return "нет значимых слов в названии"
    rows = (client.table("tenders").select("source,title,price,deadline,alert_seq")
            .neq("source", t.source).ilike("title", "%" + sorted(words, key=len)[-1] + "%")
            .order("collected_at", desc=True).limit(30).execute().data) or []
    hits = []
    for r in rows:
        rw = _extract_significant_words(r.get("title") or "")
        if rw and len(words & rw) / min(len(words), len(rw)) >= 0.6:
            hits.append({"source": r.get("source"), "title": (r.get("title") or "")[:60],
                         "price": r.get("price"), "deadline": r.get("deadline")})
    return json.dumps(hits[:5], ensure_ascii=False) if hits else "на других площадках не найден"


async def investigate(tender, db_client):
    """Bounded agent loop. Returns the verdict dict or None."""
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic SDK not installed: .venv/bin/pip install anthropic")
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set in .env — investigator dormant")
        return None
    client = anthropic.AsyncAnthropic(api_key=api_key)

    ctx = ("Лот: %s\nЗаказчик: %s\nЦена: %s %s\nДедлайн: %s\nИсточник: %s\nURL: %s\nExtra: %s"
           % (tender.title, tender.organization, tender.price, tender.currency,
              tender.deadline, tender.source, tender.source_url,
              json.dumps(tender.extra_info, ensure_ascii=False)))
    messages = [{"role": "user", "content": "Разбери этот лот и дай вердикт.\n\n" + ctx}]
    usage_in = usage_out = 0

    for turn in range(MAX_TURNS):
        resp = await client.messages.create(
            model=MODEL, max_tokens=1500, system=SYSTEM, tools=TOOLS, messages=messages)
        usage_in += resp.usage.input_tokens
        usage_out += resp.usage.output_tokens
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": "Вызови submit_verdict с финальным вердиктом."})
            continue
        results = []
        verdict = None
        for tu in tool_uses:
            if tu.name == "submit_verdict":
                verdict = tu.input
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "принято"})
            elif tu.name == "fetch_lot_detail":
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": await _tool_fetch_detail(tender)})
            elif tu.name == "check_lot_alive":
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": await _tool_check_alive(tender)})
            elif tu.name == "find_cross_platform":
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": _tool_cross_platform(tender, db_client)})
        if verdict is not None:
            cost = usage_in / 1e6 * 3.0 + usage_out / 1e6 * 15.0  # sonnet-5 list price ceil
            verdict["_cost_usd"] = round(cost, 3)
            verdict["_turns"] = turn + 1
            return verdict
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": results})
    logger.warning("max turns reached without verdict")
    return None


def _format_verdict_msg(seq, v):
    emoji = {"участвовать": "🟢", "пропустить": "🔴", "уточнить": "🟡"}.get(v.get("verdict"), "❔")
    parts = ["🔍 *Разбор лота #%s*" % seq,
             "%s *Вердикт: %s*" % (emoji, v.get("verdict", "?").upper()),
             v.get("why", "")]
    if v.get("deadline_note"):
        parts.append("⏰ %s" % v["deadline_note"])
    if v.get("prepare"):
        parts.append("📋 Подготовить:\n" + "\n".join("  • " + p for p in v["prepare"][:5]))
    if v.get("risks"):
        parts.append("⚠️ Риски:\n" + "\n".join("  • " + r for r in v["risks"][:3]))
    return "\n".join(p for p in parts if p)


async def _send_tg_reply(text, reply_to_msg_id=None):
    async with httpx.AsyncClient(timeout=15) as cl:
        payload = {"chat_id": settings.telegram_alert_chat_id, "text": text,
                   "parse_mode": "Markdown", "disable_web_page_preview": True}
        if reply_to_msg_id:
            payload["reply_to_message_id"] = reply_to_msg_id
            payload["allow_sending_without_reply"] = True
        r = await cl.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                          json=payload)
    return r.status_code == 200


def _daily_count(store):
    st = store.get_setting(STATE_KEY) or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return st, (st.get(today) or {}).get("count", 0), today


async def run_one(seq, db_client, store):
    row = (db_client.table("tenders")
           .select("id,external_id,title,organization,price,currency,deadline,source,source_url,search_text,message_type,extra_info,telegram_message_id")
           .eq("alert_seq", seq).limit(1).execute().data or [None])[0]
    if not row:
        logger.error("alert #%s not found", seq)
        return False
    t = _row_to_tender(row)
    logger.info("investigating #%s: %s", seq, (t.title or "")[:50])
    v = await investigate(t, db_client)
    if not v:
        return False
    ok = await _send_tg_reply(_format_verdict_msg(seq, v), row.get("telegram_message_id"))
    # log to state
    st, cnt, today = _daily_count(store)
    day = st.setdefault(today, {"count": 0, "runs": []})
    day["count"] = cnt + 1
    day["runs"] = (day.get("runs") or [])[-20:] + [{"seq": seq, "verdict": v.get("verdict"),
                                                    "cost": v.get("_cost_usd"), "turns": v.get("_turns")}]
    store.set_setting(STATE_KEY, st)
    logger.info("verdict=%s cost=$%s turns=%s tg=%s", v.get("verdict"), v.get("_cost_usd"), v.get("_turns"), ok)
    return ok


async def main(args):
    from crawler.core.db import _get_client
    from crawler.auth.session_store import session_store
    c = _get_client()
    if args.seq:
        return 0 if await run_one(args.seq, c, session_store) else 1
    # --auto: recent big pushed alerts not yet investigated
    st, cnt, today = _daily_count(session_store)
    if cnt >= DAILY_CAP:
        logger.info("daily cap %d reached", DAILY_CAP)
        return 0
    done_seqs = {r.get("seq") for d in st.values() if isinstance(d, dict) for r in (d.get("runs") or [])}
    rows = (c.table("tenders").select("alert_seq,price")
            .not_.is_("alert_seq", "null").gte("price", PRICE_TRIGGER)
            .order("alert_seq", desc=True).limit(20).execute().data) or []
    todo = [r["alert_seq"] for r in rows if r["alert_seq"] not in done_seqs][: DAILY_CAP - cnt]
    logger.info("auto: %d big lots to investigate", len(todo))
    for seq in todo:
        await run_one(seq, c, session_store)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", type=int, help="investigate alert #N")
    ap.add_argument("--auto", action="store_true", help="scan recent big alerts (cron)")
    a = ap.parse_args()
    if not (a.seq or a.auto):
        ap.error("--seq N or --auto required")
    sys.exit(asyncio.run(main(a)))
