"""⚔️ Bid-war monitor (2026-07-03) — watch OUR e-shop lots + spawned auctions.

The mechanic (verified live on Winch's lot 7628192 «Блокнот»): we post a supply
position in the xt-xarid/hayotbirja e-shop; a BUYER activates it → a reverse
auction (reduction) spawns where rival suppliers can undercut our price. Lot
7628192 was lost exactly this way — nobody was watching the live auction.

What this does every cron tick (*/2 min, pure RPC, zero LLM cost):
  1. AUTO-DISCOVER our positions: ref_online_shop_public filters={vendor:
     "WINCH GROUP XK"} (server-side filter verified 2026-07-03 → our 2 Блокнот
     ads). New/removed/price-changed ads are announced.
  2. Scan live reductions (ref_reduction_object_public, page 1) and match their
     goods against OUR ad product names (targeted watch-list join — few known
     products, normalized-word match; NOT the noisy general e-shop join).
  3. State machine per matched reduction (state in crawler_settings):
       NEW      → 🆕 «Аукцион по нашему товару ЗАПУЩЕН» (+price, bidders, timer)
       PRICE ↓  → ⚔️ «ПЕРЕБИВАЮТ: цена упала X → Y (наша Z), осталось ~N мин»
       GONE     → 🏁 «Аукцион закрылся: финальная цена X» (won/lost unknown
                  anonymously — the platform hides the winner)
  4. Price time-series appended to logs/our_lots_history.jsonl (auction intel).

Alert latency ≤ 2 min — enough for a human to counter-bid (перебить) manually
via the deep link. Full event log (log_procedure) is auth-walled — a future
upgrade once Daniyar's xt-xarid session is wired in.

Usage: python3 -m crawler.scripts.watch_our_lots [--dry-run]
Cron:  */2 * * * *
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("watch_our_lots")

XT = "https://api.xt-xarid.uz"
OUR_VENDOR = "WINCH GROUP XK"
STATE_KEY = "our_lots_watch_v1"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HISTORY = os.path.join(REPO_ROOT, "logs", "our_lots_history.jsonl")

# Words too generic to identify OUR product on their own (avoid false «наш товар»
# alarms on someone else's unrelated notebook auction word-collisions).
_STOP = {"шт", "дона", "для", "и", "в", "на", "с"}


def _words(s):
    import re
    return {w for w in re.findall(r"[а-яёa-z0-9]+", (s or "").lower()) if w not in _STOP and len(w) > 2}


async def _rpc(client, method, params, path="/rpc"):
    r = await client.post(XT + path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    j = r.json()
    return j.get("result")


def _fmt_price(p):
    try:
        return "{:,.0f}".format(float(p)).replace(",", " ")
    except (TypeError, ValueError):
        return str(p)


def _remain_str(rt):
    try:
        rt = int(rt)
    except (TypeError, ValueError):
        return "?"
    if rt <= 0:
        return "закрывается"
    if rt < 3600:
        return "~%d мин" % max(1, rt // 60)
    return "~%d ч" % (rt // 3600)


async def _send_tg(text):
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return False
    async with httpx.AsyncClient(timeout=15) as cl:
        r = await cl.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                          json={"chat_id": settings.telegram_alert_chat_id, "text": text,
                                "parse_mode": "Markdown", "disable_web_page_preview": True})
    return r.status_code == 200


def _append_history(rec):
    try:
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        with open(HISTORY, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except IOError:
        pass


async def tick(dry_run=False):
    from crawler.auth.session_store import session_store
    state = session_store.get_setting(STATE_KEY)
    if not isinstance(state, dict):
        state = {"ads": {}, "auctions": {}}
    ads_state = state.setdefault("ads", {})
    auc_state = state.setdefault("auctions", {})
    now_iso = datetime.now(timezone.utc).isoformat()
    alerts = []

    async with httpx.AsyncClient(timeout=20) as client:
        # ── 1. Our ads (auto-discovery, server-side vendor filter) ──
        ads = await _rpc(client, "ref", {"ref": "ref_online_shop_public", "op": "read",
                                         "limit": 100, "offset": 0,
                                         "filters": {"vendor": OUR_VENDOR}}) or []
        our_products = []  # (ad_id, name, our_price, word-set)
        seen_ids = set()
        is_seed = not ads_state  # first-ever run: baseline silently, no announces
        for a in ads:
            aid = str(a.get("id"))
            seen_ids.add(aid)
            name = a.get("product_name") or ""
            price = a.get("price")
            our_products.append((aid, name, price, _words(name)))
            prev = ads_state.get(aid)
            if prev is None:
                if not is_seed:
                    alerts.append("📌 Наша позиция в э-магазине: *%s* — %s сум\nhttps://xt-xarid.uz/procedure/%s/core"
                                  % (name, _fmt_price(price), aid))
            elif prev.get("price") != price:
                alerts.append("✏️ Цена нашей позиции изменилась: *%s* %s → %s сум"
                              % (name, _fmt_price(prev.get("price")), _fmt_price(price)))
            ads_state[aid] = {"name": name, "price": price, "seen": now_iso}
        for aid in list(ads_state):
            if aid not in seen_ids:
                alerts.append("⚠️ Наша позиция ИСЧЕЗЛА из э-магазина: *%s* (id %s) — снята или срок истёк"
                              % (ads_state[aid].get("name", "?"), aid))
                del ads_state[aid]

        # ── 2. Live reductions matching OUR products ──
        reds = await _rpc(client, "ref", {"ref": "ref_reduction_object_public", "op": "read",
                                          "limit": 200, "offset": 0}) or []
        live_matched = set()
        for r in reds:
            rid = str(r.get("id"))
            goods = r.get("good_list") or []
            gm = (r.get("meta") or {}).get("good_maps") or []
            gtext = " ".join(str(g.get("name", "")) for g in (goods if isinstance(goods, list) else []) if isinstance(g, dict))
            gtext += " " + " ".join(str(g.get("name", "")) for g in (gm if isinstance(gm, list) else []) if isinstance(g, dict))
            gw = _words(gtext)
            if not gw:
                continue
            hit = None
            for aid, name, our_price, aw in our_products:
                if aw and len(aw & gw) / len(aw) >= 0.8:  # ≥80% of OUR product words present
                    hit = (aid, name, our_price)
                    break
            if not hit:
                continue
            live_matched.add(rid)
            last_price = r.get("last_price") or r.get("start_price")
            part = r.get("part_count") or 0
            remain = r.get("remain_time")
            url = "https://xt-xarid.uz/procedure/%s/core" % rid
            prev = auc_state.get(rid)
            _append_history({"ts": now_iso, "reduction": rid, "ad": hit[0], "price": last_price,
                             "part_count": part, "remain": remain})
            if prev is None:
                alerts.append("🆕 *АУКЦИОН ПО НАШЕМУ ТОВАРУ ЗАПУЩЕН*\n"
                              "Товар: *%s* (наша цена %s сум)\n"
                              "Текущая цена: %s сум · Участников: %s · ⏳ %s\n%s"
                              % (hit[1], _fmt_price(hit[2]), _fmt_price(last_price), part, _remain_str(remain), url))
            else:
                try:
                    dropped = float(last_price) < float(prev.get("price"))
                except (TypeError, ValueError):
                    dropped = False
                if dropped:
                    alerts.append("⚔️ *ПЕРЕБИВАЮТ НАШ ЛОТ!*\n"
                                  "Товар: *%s*\nЦена упала: %s → *%s* сум (наша: %s)\n"
                                  "Участников: %s · ⏳ %s\n👉 Перебить: %s"
                                  % (hit[1], _fmt_price(prev.get("price")), _fmt_price(last_price),
                                     _fmt_price(hit[2]), part, _remain_str(remain), url))
            auc_state[rid] = {"ad": hit[0], "product": hit[1], "price": last_price,
                              "part": part, "seen": now_iso}

        # ── 3. Watched auctions that disappeared = closed ──
        for rid in list(auc_state):
            if rid not in live_matched:
                a = auc_state.pop(rid)
                alerts.append("🏁 Аукцион по нашему товару *%s* завершён. Финальная цена: %s сум "
                              "(итог смотри в кабинете)\nhttps://xt-xarid.uz/procedure/%s/core"
                              % (a.get("product", "?"), _fmt_price(a.get("price")), rid))

    if dry_run:
        print("ads=%d matched_live_auctions=%d alerts=%d" % (len(ads_state), len(live_matched), len(alerts)))
        for m in alerts:
            print("---\n" + m)
        return 0
    for m in alerts:
        await _send_tg(m)
    from crawler.auth.session_store import session_store as ss
    ss.set_setting(STATE_KEY, state)
    if alerts:
        logger.info("[OurLots] sent %d alerts", len(alerts))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(tick(a.dry_run)))
