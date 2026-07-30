"""funnel_watchdog — сторож на саму воронку алертов.

Дыра, которую это закрывает (разбор 2026-07-29/30). За пять недель поток алертов
упал 84 → 14 в день, и НИ ОДИН сторож этого не заметил: zero_result_tracker
следит за источниками, вернувшими 0 в цикле; freshness_watchdog — за источниками,
переставшими собирать; quality_tracker — за составом собранного. Между «сбор
исправен» и «алерты приходят» не было ничего. Каждая июльская правка на точность
(снятие э-магазина с push, авто-мьюты, гейт лидов, verification gate) мерилась
в одиночку и была права; их сумма срезала поток в шесть раз, и увидел это только
человек, вручную, через месяц.

Что проверяет — три оси, потому что падение объёма само по себе не говорит, где
оно родилось:

  A. ОБЪЁМ   — алертов/день за последние DAYS_RECENT дней против базы
               (предыдущие DAYS_BASE дней). Тревога на обвале или ниже пола.
  B. СТАДИИ  — из crawl_runs три сквозные доли: новых/собрано, AI/новых,
               алертов/AI. Показывает, НА КАКОЙ стадии сузилось горло: горло
               выше AI (мало вызовов) и горло в AI (много вызовов, мало
               принятых) — разные болезни с разным лечением.
  C. ИСТОЧНИКИ — кто давал алерты в базовом окне и замолчал в свежем. Именно так
               видно осознанное снятие источника с push (XT-Xarid э-магазин
               284 → 0) рядом с неосознанной потерей (Tender.mc.uz 12 → 0).

Дата алерта берётся по `collected_at`: отдельной метки времени отправки в схеме
нет, а лот в норме алертится в том же цикле, в котором впервые собран. Для
ретро-досылок (backfill/recheck) прокси врёт — это допущение, не факт.

Осознанно снятые источники не должны звенеть: список берётся ИЗ КОДА
(`_NO_PUSH_SOURCES`), а не копией, чтобы не разъезжался с продом.

  --dry-run   печатает всю таблицу, ничего не шлёт и не пишет состояние
  --as-of D   считать, как будто сегодня D (бэктест: звенел бы сторож в тот день)

Крон (host): 30 6 * * * cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.funnel_watchdog
Выход 1 — если хоть одна тревога (видно в логе крона).
"""
import argparse
import asyncio
import collections
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.auth.session_store import session_store
from crawler.config.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("funnel-watchdog")

STATE_KEY = "funnel_watchdog_state"

DAYS_RECENT = 7          # свежее окно
DAYS_BASE = 28           # база — предыдущие 28 дней (не включая свежее окно)
DROP_PCT = 50            # падение алертов/день, при котором звеним
FLOOR_PER_DAY = 8.0      # абсолютный пол: меньше — звеним независимо от базы
STAGE_DROP_PCT = 45      # падение сквозной доли стадии
MIN_SRC_ALERTS = 5       # источник считаем «дававшим», если в базе было столько
MIN_BASE_DAYS = 14       # меньше данных в базе — сравнивать нечестно
MAX_SILENT_ALARMS = 4    # больше — сворачиваем в одну строку, чтобы не глушить

# Проверка на сползание. Бэктест 30.07 показал: порога «−50% к базе 28 дней»
# мало. Он звенел бы 26.07 — на три дня раньше человека, и то потому, что к
# этому дню обвал стал резким. На 12.07 падение было −39.6%, на 19.07 −45.8%,
# и сторож молчал бы обе недели, потому что база сама съезжает вместе с трендом.
# Понижать порог до 30% нельзя: недельный разброс достигает ±20% (на 05.07
# сравнение дало +20% роста). Поэтому вторая, независимая проверка — форма
# кривой: три недельных шага подряд вниз и суммарное падение ≥ TREND_DROP_PCT.
# Разброс так себя не ведёт, а пятинедельное сползание — ровно так.
DAYS_TREND = 56          # окно для недельных корзин
TREND_WEEKS = 3          # столько шагов подряд вниз считаем сползанием
TREND_DROP_PCT = 30      # и суммарное падение от начала серии

FIELDS_ALERTED = "source,collected_at,alert_seq"
RUN_FIELDS = ("started_at,total_fetched,total_new,alerts_sent,ai_calls_count,dry_run")


def _no_push_sources():
    # type: () -> frozenset
    """Осознанно снятые с push — из кода нотифаера, не копией."""
    try:
        from crawler.core.notifier import _NO_PUSH_SOURCES
        return frozenset(_NO_PUSH_SOURCES)
    except Exception as exc:                      # pragma: no cover
        logger.warning("не смог прочитать _NO_PUSH_SOURCES: %s", str(exc)[:80])
        return frozenset()


# Источники, чьё молчание объяснено и не требует тревоги. Только с датой и
# причиной, проверенной на данных — иначе список превращается в свалку, которая
# глушит сторожа. Каждая строка ниже подтверждена выборкой 30.07.
KNOWN_SILENT = {
    "E-Birja завершённые сделки":
        "фид завершённых сделок: 99 из 102 его алертов были по уже закрытым лотам (30.07)",
    # Новостные каналы министерств. Прежние 240 алертов за 28 дней — это были
    # новости: «Valyutalar kursi», «🌱🌱🌱», «#BU_MUHIM», посты без цены. Молчание
    # здесь — рост точности, а не потеря спроса (выборка 30.07: свежих строк
    # десятки, цена почти всегда пустая, тип tender).
    "TG: Мин сельхоз": "новостной канал — прежние алерты были новостями (30.07)",
    "TG: Минстрой": "новостной канал — прежние алерты были новостями (30.07)",
    "TG: Минздрав": "новостной канал — прежние алерты были новостями (30.07)",
    "TG: Хамкорбанк": "новостной канал (курс валют) — прежние алерты были новостями (30.07)",
    "TG: Мин ИТ": "новостной канал — прежние алерты были новостями (30.07)",
    "TG: Комитет экологии": "новостной канал — прежние алерты были новостями (30.07)",
    "Tender.mc.uz (Минстрой)":
        "стройка и ремонт, не наш профиль: 5 алертов на 5 047 строк за 35 дн (30.07)",
    "E-Birja товары на продажу": "предложения продавцов, а не закупка (30.07)",
}


def _windows(as_of):
    # type: (datetime) -> Tuple[str, str, str]
    """(начало базы, начало свежего окна, конец) в ISO — полуинтервалы [a,b)."""
    end = as_of
    recent_start = end - timedelta(days=DAYS_RECENT)
    base_start = recent_start - timedelta(days=DAYS_BASE)
    return base_start.isoformat(), recent_start.isoformat(), end.isoformat()


def _fetch_runs(base_start, end):
    # type: (str, str) -> List[Dict]
    from crawler.core.db import _get_client, query_with_retry
    c = _get_client()
    rows = []           # type: List[Dict]
    off = 0
    while off < 20000:
        def _q(o=off):
            return (c.table("crawl_runs").select(RUN_FIELDS)
                    .gte("started_at", base_start).lt("started_at", end)
                    .order("started_at").range(o, o + 999).execute())
        got = query_with_retry(_q, label="runs").data or []
        rows.extend(got)
        if len(got) < 1000:
            break
        off += 1000
    return [r for r in rows if not r.get("dry_run")]


def _fetch_alerted(base_start, end):
    # type: (str, str) -> List[Dict]
    """Отправленные строки за окно. Окнами по суткам: сортированный OFFSET на
    десятки тысяч строк стабильно ловит 57014."""
    from crawler.core.db import iter_rows
    d0 = datetime.fromisoformat(base_start).date()
    d1 = datetime.fromisoformat(end).date()
    rows = []           # type: List[Dict]
    day = d0
    while day < d1 + timedelta(days=1):
        nxt = day + timedelta(days=1)
        # `alert_seq >= 1` вместо `IS NOT NULL`: iter_rows принимает только
        # одноуровневые методы билдера, а NULL не проходит числовое сравнение.
        for page in iter_rows("tenders", FIELDS_ALERTED,
                              filters=[("gte", ("collected_at", day.isoformat())),
                                       ("lt", ("collected_at", nxt.isoformat())),
                                       ("gte", ("alert_seq", 1))],
                              label="alerted %s" % day, max_pages=10):
            rows.extend(page)
        day = nxt
    return rows


def _per_day(rows, key_ts, base_start, recent_start, end):
    # type: (List[Dict], str, str, str, str) -> Tuple[float, float, int, int]
    """(база в день, свежее в день, дней базы, дней свежего) по числу строк."""
    b = sum(1 for r in rows if base_start <= str(r.get(key_ts) or "") < recent_start)
    r_ = sum(1 for r in rows if recent_start <= str(r.get(key_ts) or "") < end)
    return b / float(DAYS_BASE), r_ / float(DAYS_RECENT), b, r_


def _sum_window(runs, lo, hi, field):
    # type: (List[Dict], str, str, str) -> int
    return sum(int(r.get(field) or 0) for r in runs
               if lo <= str(r.get("started_at") or "") < hi)


def _pct_drop(base, recent):
    # type: (float, float) -> Optional[float]
    if base <= 0:
        return None
    return round((base - recent) / base * 100.0, 1)


def _delta(drop):
    # type: (Optional[float]) -> str
    """Подпись к дельте. Отрицательное падение — это рост, и печатать его как
    «−-85.5%» (первая версия так и делала) значит врать глазу читателя."""
    if drop is None or drop == 0:
        return ""
    if drop > 0:
        return " (−%.1f%%)" % drop
    return " (+%.1f%% роста)" % abs(drop)


def weekly_buckets(runs, end, weeks):
    # type: (List[Dict], datetime, int) -> List[Dict]
    """Алертов в день по недельным корзинам, от старой к свежей.

    Корзины считаются назад от `end` семидневками, поэтому последняя корзина —
    это ровно свежее окно из проверки A.
    """
    out = []            # type: List[Dict]
    for k in range(weeks, 0, -1):
        hi = (end - timedelta(days=7 * (k - 1))).isoformat()
        lo = (end - timedelta(days=7 * k)).isoformat()
        alerts = _sum_window(runs, lo, hi, "alerts_sent")
        days = len(set(str(r.get("started_at"))[:10] for r in runs
                       if lo <= str(r.get("started_at") or "") < hi))
        out.append({"from": lo[:10], "to": hi[:10], "alerts": alerts,
                    "days": days, "per_day": round(alerts / 7.0, 1)})
    return out


def trend_slide(buckets):
    # type: (List[Dict]) -> Optional[Dict]
    """Сползание: TREND_WEEKS шагов подряд вниз и суммарное падение от начала
    серии ≥ TREND_DROP_PCT. Возвращает описание или None.

    Смотрим ровно хвост: сползание — это то, что происходит сейчас, а не то, что
    когда-то было в середине окна.
    """
    need = TREND_WEEKS + 1
    tail = [b for b in buckets if b["days"] > 0][-need:]
    if len(tail) < need:
        return None
    for i in range(1, len(tail)):
        if tail[i]["per_day"] >= tail[i - 1]["per_day"]:
            return None
    drop = _pct_drop(tail[0]["per_day"], tail[-1]["per_day"])
    if drop is None or drop < TREND_DROP_PCT:
        return None
    return {"weeks": len(tail) - 1, "drop": drop,
            "path": [b["per_day"] for b in tail],
            "from": tail[0]["from"], "to": tail[-1]["to"]}


def silent_sources(src_base, src_recent, nopush=None):
    # type: (Dict, Dict, Optional[frozenset]) -> List[Dict]
    """Источники, дававшие алерты в базе и умолкшие в свежем окне.

    `excused` заполняется, когда молчание объяснено: источник снят с push в коде
    прода либо занесён в KNOWN_SILENT с причиной. Объяснимое печатаем, но не
    звеним — иначе сторож захлебнётся в собственных осознанных решениях.
    """
    nopush = _no_push_sources() if nopush is None else nopush
    out = []            # type: List[Dict]
    for s, n in sorted(src_base.items(), key=lambda kv: -kv[1]):
        if src_recent.get(s):
            continue
        if n < MIN_SRC_ALERTS:
            continue
        out.append({
            "source": s, "was": n,
            "excused": ("снят с push в коде" if s in nopush
                        else KNOWN_SILENT.get(s)),
        })
    return out


def analyze(as_of):
    # type: (datetime) -> Dict
    base_start, recent_start, end = _windows(as_of)
    # crawl_runs тянем шире окон сравнения — на недельные корзины тренда.
    # Таблица маленькая, лишние три недели ничего не стоят.
    trend_start = (as_of - timedelta(days=DAYS_TREND)).isoformat()
    runs = _fetch_runs(min(trend_start, base_start), end)
    alerted = _fetch_alerted(base_start, end)

    days_with_runs = len(set(str(r.get("started_at"))[:10] for r in runs
                            if str(r.get("started_at") or "") < recent_start))

    # A. Объём — по crawl_runs (это факт отправки, а не прокси).
    a_base = _sum_window(runs, base_start, recent_start, "alerts_sent")
    a_recent = _sum_window(runs, recent_start, end, "alerts_sent")
    per_base = a_base / float(DAYS_BASE)
    per_recent = a_recent / float(DAYS_RECENT)
    vol_drop = _pct_drop(per_base, per_recent)

    # B. Стадии — сквозные доли на одних и тех же окнах.
    #
    # Звеним ТОЛЬКО на «алертов / новых»: это сквозная конверсия, у неё нет
    # двусмысленности. «AI / новых» печатаем справочно и не звеним — счётчик
    # ai_calls_count складывает вызовы обогащения и вызовы релевантности
    # (runner: log_enrichment(..., ai_calls=...)), поэтому доля от него читается
    # неоднозначно: первая версия сторожа на нём выдала «алертов / AI = 1.234»,
    # то есть алертов больше, чем вызовов.
    stages = []         # type: List[Dict]
    for name, num, den, alarm in (
            ("алертов / новых", "alerts_sent", "total_new", True),
            ("новых / собрано", "total_new", "total_fetched", False),
            ("AI / новых", "ai_calls_count", "total_new", False)):
        nb = _sum_window(runs, base_start, recent_start, num)
        db_ = _sum_window(runs, base_start, recent_start, den)
        nr = _sum_window(runs, recent_start, end, num)
        dr = _sum_window(runs, recent_start, end, den)
        rb = (nb / float(db_)) if db_ else None
        rr = (nr / float(dr)) if dr else None
        stages.append({
            "name": name, "base": rb, "recent": rr, "alarm": alarm,
            "drop": _pct_drop(rb, rr) if (rb and rr is not None) else None,
            "num_base": nb, "den_base": db_, "num_recent": nr, "den_recent": dr,
        })

    # C. Источники — по прокси collected_at.
    src_base = collections.Counter()
    src_recent = collections.Counter()
    for r in alerted:
        ts = str(r.get("collected_at") or "")
        s = r.get("source") or "—"
        if base_start <= ts < recent_start:
            src_base[s] += 1
        elif recent_start <= ts < end:
            src_recent[s] += 1
    silent = silent_sources(src_base, src_recent)

    # D. Сползание — независимая от базы проверка формы кривой.
    buckets = weekly_buckets(runs, as_of, DAYS_TREND // 7)

    return {
        "as_of": end, "base_start": base_start, "recent_start": recent_start,
        "days_with_runs_in_base": days_with_runs,
        "alerts_base": a_base, "alerts_recent": a_recent,
        "per_day_base": round(per_base, 1), "per_day_recent": round(per_recent, 1),
        "volume_drop_pct": vol_drop,
        "stages": stages, "silent_sources": silent,
        "weekly": buckets, "slide": trend_slide(buckets),
        "src_base": dict(src_base), "src_recent": dict(src_recent),
    }


def verdict(rep):
    # type: (Dict) -> Tuple[List[str], List[str]]
    """(тревоги, пометки). Тревога — то, что требует разбора сегодня."""
    alarms = []         # type: List[str]
    notes = []          # type: List[str]

    if rep["days_with_runs_in_base"] < MIN_BASE_DAYS:
        notes.append("база короткая (%d дн с прогонами) — сравнение не звеню"
                     % rep["days_with_runs_in_base"])
        return alarms, notes

    d = rep["volume_drop_pct"]
    if d is not None and d >= DROP_PCT:
        alarms.append("объём: %.1f → %.1f алертов/день (−%.1f%% к базе %d дн)"
                      % (rep["per_day_base"], rep["per_day_recent"], d, DAYS_BASE))
    if rep["per_day_recent"] < FLOOR_PER_DAY:
        alarms.append("объём ниже пола: %.1f алертов/день (пол %.1f)"
                      % (rep["per_day_recent"], FLOOR_PER_DAY))

    sl = rep.get("slide")
    if sl:
        alarms.append("сползание: %d недели подряд вниз, %s алертов/день (−%.1f%% с %s)"
                      % (sl["weeks"], " → ".join("%.1f" % v for v in sl["path"]),
                         sl["drop"], sl["from"]))

    for st in rep["stages"]:
        if not st.get("alarm"):
            continue
        if st["drop"] is not None and st["drop"] >= STAGE_DROP_PCT:
            alarms.append("стадия «%s»: %.3f → %.3f (−%.1f%%)"
                          % (st["name"], st["base"], st["recent"], st["drop"]))

    # Замолчавшие источники: звеним по самым весомым, остальное — одной строкой.
    # Девять пунктов в тревоге читаются как стена и глушат сигнал.
    unexcused = [s for s in rep["silent_sources"] if not s["excused"]]
    for s in unexcused[:MAX_SILENT_ALARMS]:
        alarms.append("источник замолчал в алертах: «%s» (было %d, стало 0)"
                      % (s["source"], s["was"]))
    if len(unexcused) > MAX_SILENT_ALARMS:
        tail = unexcused[MAX_SILENT_ALARMS:]
        alarms.append("ещё %d источника замолчали (%d алертов в базе): %s"
                      % (len(tail), sum(s["was"] for s in tail),
                         ", ".join(s["source"] for s in tail)))
    for s in rep["silent_sources"]:
        if s["excused"]:
            notes.append("молчит объяснимо: «%s» (было %d) — %s"
                         % (s["source"], s["was"], s["excused"]))
    return alarms, notes


def render(rep, alarms, notes):
    # type: (Dict, List[str], List[str]) -> str
    L = []
    head = "\U0001f6a8 *Воронка алертов просела*" if alarms else "✅ *Воронка алертов в норме*"
    L.append(head)
    L.append("окна: база %s…%s, свежее %s…%s"
             % (rep["base_start"][:10], rep["recent_start"][:10],
                rep["recent_start"][:10], rep["as_of"][:10]))
    L.append("алертов/день: *%.1f* → *%.1f*%s"
             % (rep["per_day_base"], rep["per_day_recent"],
                _delta(rep["volume_drop_pct"])))
    wk = [b for b in rep.get("weekly", []) if b["days"] > 0]
    if wk:
        L.append("по неделям: %s"
                 % " → ".join("%.1f" % b["per_day"] for b in wk[-5:]))
    L.append("")
    L.append("*Стадии* (доля прохождения):")
    for st in rep["stages"]:
        b = "—" if st["base"] is None else "%.3f" % st["base"]
        r = "—" if st["recent"] is None else "%.3f" % st["recent"]
        L.append("• %s: %s → %s%s" % (st["name"], b, r, _delta(st["drop"])))
    if alarms:
        L.append("")
        L.append("*Тревоги:*")
        for a in alarms:
            L.append("• %s" % a)
    if notes:
        L.append("")
        L.append("_Пометки:_")
        for n in notes:
            L.append("_• %s_" % n)
    return "\n".join(L)


async def _send_telegram(text):
    # type: (str) -> bool
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("нет telegram-конфига — не отправляю")
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.post(url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text, "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
            if resp.status_code != 200:
                logger.warning("telegram %d: %s", resp.status_code, resp.text[:200])
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("telegram упал: %s", str(exc)[:120])
        return False


async def main(dry_run=False, as_of=None):
    # type: (bool, Optional[str]) -> int
    ref = (datetime.fromisoformat(as_of).replace(tzinfo=timezone.utc)
           if as_of else datetime.now(timezone.utc))
    rep = analyze(ref)
    alarms, notes = verdict(rep)

    logger.info("алертов/день: %.1f → %.1f (падение %s%%)",
                rep["per_day_base"], rep["per_day_recent"], rep["volume_drop_pct"])
    for st in rep["stages"]:
        logger.info("   стадия %-16s %s → %s (падение %s%%)", st["name"],
                    None if st["base"] is None else round(st["base"], 4),
                    None if st["recent"] is None else round(st["recent"], 4),
                    st["drop"])
    for s in rep["silent_sources"]:
        logger.info("   замолчал: %-42s было %d%s", s["source"][:42], s["was"],
                    "  [объяснимо: %s]" % s["excused"] if s["excused"] else "")
    for a in alarms:
        logger.warning("ТРЕВОГА: %s", a)
    for n in notes:
        logger.info("пометка: %s", n)

    if dry_run:
        logger.info("DRY RUN — без Telegram и без записи состояния")
        print()
        print(render(rep, alarms, notes))
        return 1 if alarms else 0

    # Шлём при смене набора тревог: молчим, пока картина та же, и отбиваем
    # выздоровление один раз.
    raw = session_store.get_setting(STATE_KEY)
    prev = set(raw.get("alarms", [])) if isinstance(raw, dict) else set()
    now_set = set(alarms)
    if now_set and now_set != prev:
        await _send_telegram(render(rep, alarms, notes))
    elif prev and not now_set:
        await _send_telegram("✅ *Воронка алертов восстановилась*\nалертов/день: %.1f"
                             % rep["per_day_recent"])
    session_store.set_setting(STATE_KEY, {
        "alarms": sorted(now_set),
        "per_day_recent": rep["per_day_recent"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return 1 if alarms else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--as-of", help="считать, как будто сегодня эта дата (бэктест)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(dry_run=args.dry_run, as_of=args.as_of)))
