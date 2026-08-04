"""recheck — второй шанс для лотов, которых конвейер не увидел с первого раза.

Дыра, которую это закрывает. `upsert_tenders` возвращает «новыми» только те
строки, у которых пары (external_id, source) ещё не было в базе, а `runner`
отдаёт в `send_alerts` ровно их. Значит промах при первом появлении вечен:
лот, не совпавший с ключевыми словами в день сбора, не будет пересмотрен НИКОГДА
— даже когда ключ добавлен на следующий день. Так умер лот Xalq Bank на
1 225 574 400 сум («kartholder»): ключ добавлен 29.07, лот собран 16.07, и
добавление ключа не вернуло его. Так же прошли мимо пять предквалификаций
Гафура Гуляма на 7-10 млрд.

Что делает: берёт лоты, которые НИКОГДА не отправлялись и которых НИКОГДА не
видел AI, прогоняет их сегодняшним префильтром, применяет продовый дедуп против
уже отправленного и досылает выжившее обычным `send_alerts`.

Почему условие «AI не видел» (relevance_score IS NULL) — механизм самоограничен:
после проверки строка получает score и в выборку больше не попадает. Без этого
условия каждый прогон переспрашивал бы AI об одном и том же и жёг деньги.

Почему дедуп обязателен: у площадок один лот лежит несколькими строками
(«Услуги издательские» — по две строки на лот), и без `group_for_alerts` второй
шанс превращается в рассылку дублей уже отправленного.

Границы, снятые замером 30.07 (14 дней, цена ≥ 5 млн, снятые источники прочь):
22 799 кандидатов → 2 076 проходят префильтр, из них 1 115 — фид завершённых
сделок (алерты по нему ошибка сами по себе, отсюда `_SKIP_SOURCES`).

  --dry-run (по умолчанию) — ноль записей и отправок: префильтр + дедуп + AI
                             через replay, печать таблицы
  --execute                 — реальная досылка через send_alerts
  --days N                  — окно сбора (10 по умолчанию: лот старше почти
                              всегда уже закрыт, а гейт дедлайна это не поймает,
                              потому что у 64% строк дедлайн пуст)
  --max N                   — сколько самых дорогих отдать в AI (кап печатается)

Крон (host): 45 6 * * * cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.recheck --execute
"""
import argparse
import asyncio
import collections
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# ai_decision_log резолвит путь при импорте модуля — переменную ставим до
# первого crawler-импорта, иначе решения этого прогона уедут в общий лог.
os.environ.setdefault("PARSING_AI_LOG", "/tmp/recheck-ai-decisions.jsonl")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("recheck")

FIELDS = ("external_id,source,title,organization,search_text,price,currency,"
          "deadline,date_start,date_end,collected_at,message_type,bid_count,"
          "status,extra_info,source_url,alert_seq,relevance_score,group_id")

# Фиды, по которым алерт не имеет смысла в принципе: это не спрос, а история.
# «E-Birja завершённые сделки» — уже закрытые сделки (нужны трекеру результатов);
# он же дал 1 115 из 2 076 выживших в замере 30.07 и 99 ложных алертов за всю
# историю прода. Второй шанс их не касается.
_SKIP_SOURCES = frozenset({
    "E-Birja завершённые сделки",
    "E-Birja товары на продажу",     # предложения продавцов, не закупка
    "UZEX Результаты",
})


def _now():
    # type: () -> datetime
    return datetime.now(timezone.utc)


def day_filters(d0, d1, min_price, no_push):
    # type: (str, str, int, frozenset) -> List
    """Условия выборки за сутки. Два условия здесь несут весь смысл механизма:
    `alert_seq IS NULL` — лот ни разу не отправлялся, `relevance_score IS NULL`
    — AI его ни разу не судил. Второе делает механизм самоограниченным: после
    проверки строка получает score и в выборку больше не попадает, поэтому
    ежедневный крон не переспрашивает AI об одном и том же.
    """
    filters = [("is_", ("alert_seq", "null")),
               ("is_", ("relevance_score", "null")),
               # Цена ЛИБО выше порога, ЛИБО пустая. Пустую добавили 04.08:
               # `price >= N` в SQL отбрасывает NULL, и второй шанс не видел
               # НИ ОДНОГО лота без цены — а это ровно банки и корпоративные
               # объявления, где цену не публикуют. Продовый префильтр их
               # пропускает (стадия MIN_PRICE: «5_000_000 и None → живут»,
               # пин в test_prefilter_parity), то есть выборка расходилась с
               # тем гейтом, который она обязана воспроизводить. Найдено при
               # разборе молчащего Anor Bank: 7 таких строк за 14 дней только
               # по банковским источникам.
               ("or_", ("price.gte.%d,price.is.null" % min_price,)),
               ("gte", ("collected_at", d0)),
               ("lt", ("collected_at", d1))]
    for src in sorted(no_push):
        filters.append(("neq", ("source", src)))
    return filters


def fetch_candidates(days, min_price):
    # type: (int, int) -> List[Dict]
    """Лоты без алерта и без вердикта AI за окно сбора.

    Пагинация окнами по суткам: сортированный OFFSET на сотню тысяч строк
    стабильно ловит 57014 (проверено 30.07 — упало на 121-й странице).
    """
    from crawler.core.db import iter_rows
    from crawler.core.notifier import _NO_PUSH_SOURCES

    today = _now().date()
    rows = []           # type: List[Dict]
    truncated = []      # type: List[str]
    for back in range(days):
        d0 = (today - timedelta(days=back + 1)).isoformat()
        d1 = (today - timedelta(days=back)).isoformat()
        filters = day_filters(d0, d1, min_price, _NO_PUSH_SOURCES)
        pages = 0
        for page in iter_rows("tenders", FIELDS, filters=filters,
                              label="recheck %s" % d0, max_pages=40):
            rows.extend(page)
            pages += 1
        if pages >= 40:
            truncated.append(d0)
    if truncated:
        logger.warning("выборка НЕПОЛНАЯ — дни, упёршиеся в кап страниц: %s",
                       ", ".join(truncated))
    return [r for r in rows if (r.get("source") or "") not in _SKIP_SOURCES]


def survivors(rows, keywords=None, tnved_scope=None):
    # type: (List[Dict], Optional[List[str]], Optional[List[str]]) -> Tuple[List, Dict[str, int]]
    """Префильтр СЕГОДНЯШНИМ днём (now=None): решаем, шлём ли сейчас, а не
    «дошло бы тогда».

    Разница не косметическая. Замер 30.07 сначала прогнали с as_of=дата сбора и
    получили 18 «дошло бы» из 60 — но механизм судит сегодня, и под сегодняшним
    днём 8 559 из 15 143 кандидатов отсеиваются как просроченные. Судить второй
    шанс прошлым днём значит досылать закрытые лоты.
    """
    from crawler.core.notifier import prefilter, _get_keywords, _load_tnved_scope
    from crawler.scripts.replay import row_to_raw_tender

    tenders = [row_to_raw_tender(r) for r in rows]
    if keywords is None:
        keywords = _get_keywords()
    if tnved_scope is None:
        tnved_scope = _load_tnved_scope()
    res = prefilter(tenders, keywords, tnved_scope=tnved_scope)
    keep = [t for t, _kw in res.matching] + [t for t, _kw in res.uzex_bypass]
    return keep, res.counters


def dedup_against_sent(tenders):
    # type: (List) -> Tuple[List, int]
    """Продовый дедуп: и внутри выборки, и против отправленного за 14 дней.

    Без этого второй шанс шлёт дубли — у площадок один лот лежит несколькими
    строками, и одну из них мы уже отправляли.
    """
    from crawler.core.dedup import group_for_alerts, load_recent_alerted_fingerprints
    recent = load_recent_alerted_fingerprints(days=14)
    deduped, group_sources = group_for_alerts(tenders, tenders,
                                              recent_alerted_keys=recent)
    return deduped, len(tenders) - len(deduped)


def _fmt(n):
    # type: (Optional[float]) -> str
    return "{:,.0f}".format(n or 0).replace(",", " ")


async def run(days, min_price, cap, execute):
    # type: (int, int, int, bool) -> int
    rows = fetch_candidates(days, min_price)
    by_id = dict((r["external_id"], r) for r in rows)
    logger.info("кандидатов (не отправлялись, AI не видел, цена ≥ %s, %d дн): %d",
                _fmt(min_price), days, len(rows))
    if not rows:
        return 0

    keep, counters = survivors(rows)
    logger.info("префильтр сегодняшним днём: прошли %d", len(keep))
    for st, n in sorted(counters.items(), key=lambda kv: -kv[1]):
        if n and st not in ("passed", "bypass"):
            logger.info("   отсев %-20s %d", st, n)
    if not keep:
        return 0

    deduped, dropped = dedup_against_sent(keep)
    logger.info("дедуп против отправленного за 14 дн: %d → %d (дублей %d)",
                len(keep), len(deduped), dropped)
    if not deduped:
        logger.info("всё было дублями уже отправленного — досылать нечего")
        return 0

    deduped.sort(key=lambda t: -(getattr(t, "price", None) or 0))
    if len(deduped) > cap:
        logger.warning("⚠️  кандидатов %d, кап %d — беру самые дорогие, "
                       "остальные НЕ проверены в этом прогоне", len(deduped), cap)
        deduped = deduped[:cap]

    if not execute:
        # Сухой прогон судит через replay: `send_alerts(dry_run=True)` выходит
        # ДО AI, то есть о вердиктах ничего не скажет.
        from crawler.scripts.replay import replay_tenders
        res = await replay_tenders(deduped, use_ai=True, as_of="now")
        good = [v for v in res if v.delivered]
        logger.info("СУХОЙ ПРОГОН: дошло бы %d из %d (ошибок AI %d)",
                    len(good), len(res), sum(1 for v in res if v.ai_error))
        for v in sorted(good, key=lambda v: -(by_id.get(v.external_id, {}).get("price") or 0)):
            r = by_id.get(v.external_id, {})
            print("\n  %15s | score=%s %s | %s" % (
                _fmt(r.get("price")), v.ai_score, v.ai_category or "", v.route or "—"))
            print("     %s" % (r.get("title") or "")[:88])
            print("     %s | собран %s | ключ %s"
                  % ((r.get("source") or "")[:36], str(r.get("collected_at"))[:10],
                     v.matched_kw))
            print("     %s" % (r.get("source_url") or ""))
        return 0

    from crawler.core.notifier import send_alerts
    pushed = await send_alerts(deduped, dry_run=False)
    delivered = _count_delivered(deduped)
    # `send_alerts` возвращает ТОЛЬКО пуши. Первая версия печатала это число как
    # «доставлено», и прогон, отправивший дайджест из 16 строк, отчитался
    # «ДОСЛАНО: 0» — отчёт занижал собственную работу. Считаем по факту в базе:
    # дайджест-строки тоже получают alert_seq.
    logger.info("ДОСЛАНО: %d из %d кандидатов (пушей %d, остальное дайджестом)",
                delivered, len(deduped), pushed)
    return delivered


def _count_delivered(tenders):
    # type: (List) -> int
    """Сколько из переданных лотов реально получили номер алерта."""
    from crawler.core.db import _get_client, query_with_retry
    ids = [getattr(t, "external_id", None) for t in tenders]
    ids = [i for i in ids if i]
    if not ids:
        return 0
    client = _get_client()
    total = 0
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]

        def _q(c=chunk):
            return (client.table("tenders").select("external_id,alert_seq")
                    .in_("external_id", c).execute())
        try:
            rows = query_with_retry(_q, label="recheck delivered").data or []
        except Exception as exc:
            logger.warning("не смог сверить доставку по базе: %s", str(exc)[:100])
            return -1
        total += sum(1 for r in rows if r.get("alert_seq") is not None)
    return total


def main():
    # type: () -> int
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--min-price", type=int, default=None,
                    help="по умолчанию порог прода MIN_PRICE")
    ap.add_argument("--max", type=int, default=40, dest="cap")
    ap.add_argument("--execute", action="store_true",
                    help="реально досылать (по умолчанию сухой прогон)")
    args = ap.parse_args()

    from crawler.core.notifier import MIN_PRICE
    min_price = args.min_price if args.min_price is not None else MIN_PRICE
    asyncio.run(run(args.days, min_price, args.cap, args.execute))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
