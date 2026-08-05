"""Freshness-SLO watchdog — alerts when a LIVE source goes unexpectedly silent.

Gap this fills: zero_result_tracker only flags sources that RAN and returned 0 this
cycle. A source whose fetcher was removed or whose upstream silently died (e.g.
Cooperation.uz Bosma — 993 rows, silent 40 days, noticed only in a manual audit)
never appears in crawl outcomes, so nothing fires. This watchdog is DB-based: it
compares each source's max(collected_at) against a silence threshold.

Вторая проверка (05.08) закрывает противоположный случай: источник собирается
каждый день и по collected_at выглядит образцово живым, но НОВЫХ строк не даёт
месяцами — upstream отдаёт один и тот же срез. Так полгода прожили
«Cooperation.uz Закупочные планы». Различает `created_at`: он ставится только
при вставке, значит max(created_at) — дата последней действительно новой строки.
Замер 05.08: таких источников десять, разрыв от 21 до 155 дней.

Signal-not-noise design:
- KNOWN_RETIRED allowlist suppresses sources confirmed dead-by-design (the 9
  Cooperation printing feeds consolidated into 'Cooperation.uz Лоты' on 27.04,
  plus orphan duplicate connectors). Audit 2026-06-06 verified replaced-not-lost.
- Only sources with >= MIN_ROWS history are considered (ignores tiny/new feeds).
- State in crawler_settings (session_store) — alert once per newly-silent source,
  recovery message once when it returns. No alert storms.

Cron (host): 0 7 * * * cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.freshness_watchdog
Usage: --dry-run (print, no Telegram/state write).
"""
import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple

import httpx

from crawler.auth.session_store import session_store
from crawler.config.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("freshness-watchdog")

STATE_KEY = "freshness_watchdog_state"
SILENCE_DAYS = 7
MIN_ROWS = 20

# Вторая проверка (05.08): источник собирается каждый день, но не рождает ни
# одной НОВОЙ строки — upstream заморожен, а по collected_at он выглядит живее
# всех живых. Так полгода жили «Cooperation.uz Закупочные планы»: эндпоинт
# ocelot стоит с 03.02.2026, мы три раза в сутки перекладывали один и тот же
# февральский срез, а freshness-проверка выше молчала — она смотрит именно
# collected_at, который перезаписывается при каждом upsert (db.py:106).
#
# Различает их `created_at`: он ставится только при ВСТАВКЕ, поэтому
# max(created_at) — дата последней по-настоящему новой строки.
#
# Порог 21 день, а не 7: у корпоративных площадок (АГМК, Трастбанк) две-три
# недели без нового тендера — норма, и на 7 днях сторож стал бы шумом. Замер
# 05.08 по 74 источникам: ниже 14д — плотный «нормальный» кластер, выше 21д —
# десять источников с разрывом 21…155 дней.
FROZEN_DAYS = 21

# Sources confirmed dead-by-design (audit 2026-06-06: replaced-not-lost — their
# products now flow through the live unified feeds). Do NOT alert on these.
KNOWN_RETIRED = frozenset({
    "Cooperation.uz Пакеты", "Cooperation.uz Блокноты/Ежедневники",
    "Cooperation.uz Полиграфия", "Cooperation.uz Конверты",
    "Cooperation.uz Стикеры/Наклейки", "Cooperation.uz Календари",
    "Cooperation.uz Этикетки", "Cooperation.uz Печать",
    "Cooperation.uz Брошюры/Буклеты", "Cooperation.uz Bosma (узб.)",
    "Минстрой (tender.mc.uz)", "E-Birja активные аукционы (xarid)",
    # 05.08: фетчер отключён — эндпоинт ocelot GetAllPlanSchedule заморожен
    # площадкой с 03.02.2026 (все 1500 верхних id уже у нас, НОВЫХ 0).
    # Планы закупок собирает живой близнец «… (filtered)» на cabinet-API.
    "Cooperation.uz Закупочные планы",
})

# Enabled-источники, у которых 0 строк в БД может быть нормой (малоактивный
# upstream), — не алертить про отсутствие. Аудит 2026-06-11.
KNOWN_EMPTY_OK = frozenset({
    "TG: Фонд предпринимательства",  # канал почти не постит (последнее — 2024)
})


def _supabase():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _enabled_sources_missing_in_db(db_source_names):
    # type: (Set[str]) -> List[str]
    """Enabled-источники из sources.yaml, которых НЕТ в БД вообще.

    Дыра, которую это закрывает (аудит 2026-06-11): hayotbirja-shop был enabled,
    но за всю историю собрал 0 строк (битый field_map) — watchdog молчал, т.к.
    сравнивает только то, что УЖЕ есть в tenders. Источник без единой строки
    невидим для freshness-проверки по max(collected_at).
    """
    import os
    from crawler.core.runner import load_sources

    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "sources.yaml",
    )
    try:
        configs = load_sources(cfg_path)  # отдаёт только enabled
    except Exception as exc:
        logger.warning("load_sources failed: %s", str(exc)[:120])
        return []
    enabled_names = {c.name for c in configs}
    missing = enabled_names - db_source_names - KNOWN_RETIRED - KNOWN_EMPTY_OK
    return sorted(missing)


def _silent_sources():
    # type: () -> Tuple[List[Dict], Set[str], List[Dict]]
    """Return (silent >= SILENCE_DAYS with >= MIN_ROWS, all DB names, raw rows).

    Сырые строки RPC отдаём наружу, чтобы проверка на замороженный upstream
    (_frozen_sources) переиспользовала их, а не звала RPC второй раз.
    """
    client = _supabase()
    rows = (client.rpc("source_freshness").execute().data) or []
    db_names = {(r.get("source") or "") for r in rows}  # type: Set[str]
    now = datetime.now(timezone.utc)
    out = []  # type: List[Dict]
    for r in rows:
        src = r.get("source") or ""
        cnt = r.get("cnt") or 0
        last = r.get("last_collected")
        if not last or cnt < MIN_ROWS or src in KNOWN_RETIRED:
            continue
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        days = (now - last_dt).days
        if days >= SILENCE_DAYS:
            out.append({"source": src, "cnt": int(cnt), "days": days, "last": last[:10]})
    out.sort(key=lambda x: -x["days"])
    return out, db_names, rows


def _frozen_sources(rows, silent_names):
    # type: (List[Dict], Set[str]) -> List[Dict]
    """Источники, которые СОБИРАЮТСЯ, но давно не дают новых строк.

    `rows` — выдача RPC source_freshness (source, cnt, last_collected).
    Для каждого живого источника спрашиваем max(created_at) — одну строку,
    сортировка по индексу; на 74 источниках это меньше минуты раз в сутки.

    Уже молчащие источники исключаем: про них сторож сказал выше, и второе
    сообщение о том же было бы дублем.
    """
    client = _supabase()
    now = datetime.now(timezone.utc)
    out = []  # type: List[Dict]
    for r in rows:
        src = r.get("source") or ""
        cnt = r.get("cnt") or 0
        last = r.get("last_collected")
        if not src or not last or cnt < MIN_ROWS:
            continue
        if src in KNOWN_RETIRED or src in silent_names:
            continue
        try:
            collected_days = (now - datetime.fromisoformat(
                last.replace("Z", "+00:00"))).days
        except (ValueError, AttributeError):
            continue
        if collected_days >= SILENCE_DAYS:
            continue  # молчит целиком — это случай первой проверки
        try:
            resp = (client.table("tenders").select("created_at")
                    .eq("source", src).order("created_at", desc=True)
                    .limit(1).execute())
            data = resp.data or []
        except Exception as exc:
            # Таймаут запроса не должен читаться как «источник в порядке»:
            # тот же капкан стоил metrics_tracker падения на 57014.
            logger.warning("frozen-check: %s — запрос не отработал (%s)",
                           src, str(exc)[:100])
            continue
        if not data or not data[0].get("created_at"):
            continue
        try:
            created_dt = datetime.fromisoformat(
                data[0]["created_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        fresh_days = (now - created_dt).days
        if fresh_days >= FROZEN_DAYS:
            out.append({"source": src, "cnt": int(cnt), "days": fresh_days,
                        "last_new": data[0]["created_at"][:10]})
    out.sort(key=lambda x: -x["days"])
    return out


async def _send_telegram(text):
    # type: (str) -> bool
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("No telegram config — skipping send")
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.post(url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text, "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Telegram send failed: %s", str(exc)[:120])
        return False


async def main(dry_run=False):
    # type: (bool) -> int
    silent, db_names, rows = _silent_sources()
    silent_names = {s["source"] for s in silent}  # type: Set[str]
    missing = _enabled_sources_missing_in_db(db_names)  # enabled, но 0 строк в БД
    missing_set = set(missing)
    frozen = _frozen_sources(rows, silent_names)
    frozen_names = {f["source"] for f in frozen}  # type: Set[str]

    raw_state = session_store.get_setting(STATE_KEY) if not dry_run else None
    prev = set(raw_state.get("silent", [])) if isinstance(raw_state, dict) else set()
    prev_missing = set(raw_state.get("missing", [])) if isinstance(raw_state, dict) else set()
    prev_frozen = set(raw_state.get("frozen", [])) if isinstance(raw_state, dict) else set()

    new_silent = [s for s in silent if s["source"] not in prev]
    revived = sorted(prev - silent_names)
    new_missing = sorted(missing_set - prev_missing)
    appeared = sorted(prev_missing - missing_set)  # начали давать строки
    new_frozen = [f for f in frozen if f["source"] not in prev_frozen]
    unfrozen = sorted(prev_frozen - frozen_names)

    logger.info("Silent>%dd: %d (new: %d, revived: %d); enabled-but-empty: %d (new: %d)",
                SILENCE_DAYS, len(silent), len(new_silent), len(revived),
                len(missing), len(new_missing))
    for s in silent:
        logger.info("   %s — %d rows, silent %dd (last %s)%s",
                    s["source"], s["cnt"], s["days"], s["last"],
                    "  [NEW]" if s["source"] not in prev else "")
    for m in missing:
        logger.info("   [EMPTY] %s — enabled, но в БД 0 строк%s",
                    m, "  [NEW]" if m in new_missing else "")
    logger.info("Frozen upstream (>%dd без новых строк): %d (new: %d, unfrozen: %d)",
                FROZEN_DAYS, len(frozen), len(new_frozen), len(unfrozen))
    for f in frozen:
        logger.info("   [FROZEN] %s — собирается, но новых строк нет %dд "
                    "(последняя новая %s)%s",
                    f["source"], f["days"], f["last_new"],
                    "  [NEW]" if f["source"] not in prev_frozen else "")

    if dry_run:
        logger.info("DRY RUN — no Telegram, no state write")
        return 0

    if new_silent:
        lines = ["\U0001f507 *Источник замолчал* (freshness-SLO >%dд):" % SILENCE_DAYS]
        for s in new_silent:
            lines.append("• *%s* — молчит %dд (последний %s, было %d строк)" %
                         (s["source"], s["days"], s["last"], s["cnt"]))
        lines.append("\n_Проверь фетчер/upstream — источник давал данные, но перестал._")
        await _send_telegram("\n".join(lines))

    if new_missing:
        lines = ["\U0001f573 *Источник enabled, но в БД ни одной строки:*"]
        for m in new_missing:
            lines.append("• *%s*" % m)
        lines.append("\n_Битый field\\_map/селектор/доступ — краулер делает тихие пустые прогоны "
                     "(кейс hayotbirja-shop: 0 строк за всю историю)._")
        await _send_telegram("\n".join(lines))

    if new_frozen:
        lines = ["\U0001f9ca *Upstream заморожен* (собирается, но новых строк нет "
                 ">%dд):" % FROZEN_DAYS]
        for f in new_frozen:
            lines.append("• *%s* — новых строк нет %dд (последняя новая %s, всего %d)" %
                         (f["source"], f["days"], f["last_new"], f["cnt"]))
        lines.append("\n_Источник выглядит живым по collected\\_at, но upstream отдаёт "
                     "один и тот же срез. Проверь эндпоинт: не переехал ли он._")
        await _send_telegram("\n".join(lines))

    if revived or appeared or unfrozen:
        await _send_telegram("\U0001f50a *Источник ожил*: %s" %
                             ", ".join(revived + appeared + unfrozen))

    session_store.set_setting(STATE_KEY, {"silent": sorted(silent_names),
                                          "missing": sorted(missing_set),
                                          "frozen": sorted(frozen_names),
                                          "updated_at": datetime.now(timezone.utc).isoformat()})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(dry_run=args.dry_run)))
