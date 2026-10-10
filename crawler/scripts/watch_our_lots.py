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

10.10.2026 — сторож перестал выжигать лимит площадки. С 07.10 с нашего IP
площадка пускает один запрос в минуту на оба домена, а сторож один делал два
запроса каждые 2 минуты — весь лимит, при том что наших позиций в э-магазине
нет (состояние {"ads": {}}). Теперь:
  • каждый запрос идёт через общую очередь `host_budget` (одна на все процессы);
  • частота по ситуации: позиций нет — проверка раз в 30 мин, позиции есть —
    раз в 10 мин, идёт аукцион по нашему товару — каждый тик cron;
  • аукционы ищутся, только когда есть что сопоставлять, и с limit=100: прежний
    limit=200 площадка отвергала с HTTP 400 на каждом тике, то есть поиск
    аукционов по нашему товару не работал ни разу;
  • сбой запроса = «не знаю», а не «пусто»: раньше пустой ответ превращался в
    «наша позиция ИСЧЕЗЛА» и «аукцион завершён».
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
from crawler.core import host_budget

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("watch_our_lots")

XT = "https://api.xt-xarid.uz"
OUR_VENDOR = "WINCH GROUP XK"
STATE_KEY = "our_lots_watch_v1"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HISTORY = os.path.join(REPO_ROOT, "logs", "our_lots_history.jsonl")

# Частота проверок, секунды (см. docstring, 10.10.2026).
_EVERY_NO_ADS_S = 30 * 60
_EVERY_ADS_S = 10 * 60
# Сколько тик готов ждать слота в общей очереди: cron */2, следующий тик всё равно придёт.
_SLOT_WAIT_S = 90.0
# Максимум строк за запрос у ref_*_public; больше — HTTP 400 bad_params.
_PAGE_LIMIT = 100

# Words too generic to identify OUR product on their own (avoid false «наш товар»
# alarms on someone else's unrelated notebook auction word-collisions).
_STOP = {"шт", "дона", "для", "и", "в", "на", "с"}


def _words(s):
    import re
    return {w for w in re.findall(r"[а-яёa-z0-9]+", (s or "").lower()) if w not in _STOP and len(w) > 2}


async def _rpc(client, method, params, path="/rpc"):
    """Один запрос через общую очередь. (ok, result): ok=False — ответа нет, а не «пусто»."""
    backend = host_budget.XT_BACKEND
    if not await host_budget.acquire(backend, max_wait=_SLOT_WAIT_S):
        logger.info("[OurLots] очередь к площадке длиннее %d с — пропускаю тик", _SLOT_WAIT_S)
        return False, None
    try:
        r = await client.post(XT + path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    except httpx.HTTPError as exc:
        logger.warning("[OurLots] %s: %s", params.get("ref"), type(exc).__name__)
        return False, None
    if r.status_code == 429:
        host_budget.penalize(backend)
    if r.status_code != 200:
        logger.warning("[OurLots] %s: HTTP %d", params.get("ref"), r.status_code)
        return False, None
    try:
        j = r.json()
    except ValueError:
        logger.warning("[OurLots] %s: ответ не JSON", params.get("ref"))
        return False, None
    if not isinstance(j, dict) or j.get("error"):
        logger.warning("[OurLots] %s: RPC error %s", params.get("ref"), str((j or {}).get("error"))[:120])
        return False, None
    result = j.get("result")
    return True, (result if isinstance(result, list) else [])


def next_check_after(now_ts, has_ads, has_live_auction):
    """Когда проверять снова (epoch-секунды)."""
    if has_live_auction:
        return now_ts  # аукцион идёт — каждый тик cron
    if has_ads:
        return now_ts + _EVERY_ADS_S
    return now_ts + _EVERY_NO_ADS_S


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
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    try:
        due_at = float(state.get("next_check_at") or 0)
    except (TypeError, ValueError):
        due_at = 0.0
    if not dry_run and now_ts < due_at:
        return 0
    ads_state = state.setdefault("ads", {})
    auc_state = state.setdefault("auctions", {})
    now_iso = now.isoformat()
    alerts = []
    live_matched = set()

    async with httpx.AsyncClient(timeout=20) as client:
        # ── 1. Our ads (auto-discovery, server-side vendor filter) ──
        ok, ads = await _rpc(client, "ref", {"ref": "ref_online_shop_public", "op": "read",
                                             "limit": _PAGE_LIMIT, "offset": 0,
                                             "filters": {"vendor": OUR_VENDOR}})
        if not ok:
            # Ответа нет — не значит «позиций нет». Состояние не трогаем,
            # следующий тик cron попробует снова.
            return 0
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
        # Нечего сопоставлять — не тратим запрос из минутного лимита площадки.
        reds_ok, reds = True, []
        if our_products or auc_state:
            reds_ok, reds = await _rpc(client, "ref", {"ref": "ref_reduction_object_public", "op": "read",
                                                       "limit": _PAGE_LIMIT, "offset": 0})
        for r in (reds or []):
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
        # Только если список аукционов действительно получен: при сбое запроса
        # «нет в ответе» не значит «закрылся».
        for rid in (list(auc_state) if reds_ok else []):
            if rid not in live_matched:
                a = auc_state.pop(rid)
                alerts.append("🏁 Аукцион по нашему товару *%s* завершён. Финальная цена: %s сум "
                              "(итог смотри в кабинете)\nhttps://xt-xarid.uz/procedure/%s/core"
                              % (a.get("product", "?"), _fmt_price(a.get("price")), rid))

    state["next_check_at"] = next_check_after(now_ts, bool(ads_state), bool(live_matched) or not reds_ok)
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
