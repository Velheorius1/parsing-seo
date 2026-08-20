"""Исход алерта: подали ли мы заявку и чем кончился лот.

Из чего выросло. Разбор 20.08.2026 месяц-к-месяцу: за полгода 7553 алерта на
567 млрд сум и НИ ОДНОЙ записи о том, что после них произошло. Колонка
`winner` заполнена у 2963 строк — и ни одна из них не алертилась: их пишет
results_tracker из CivilContracts, а это ПРЯМЫЕ ДОГОВОРЫ, другое пространство
id (`2612 0000 <contract_id>` против `2612 0012 <lot_id>` у лотов). То есть
«победители» и «наши алерты» никогда и не могли пересечься.

Из-за этого система не могла отличить два совершенно разных мира:
  • она работает вхолостую — показывает то, на что никто не подаёт;
  • Данияр участвует, просто мимо системы, и она об этом не знает.
Пока разницы не видно, оптимизация точности — выбор между двумя числами, ни
одно из которых не связано с выручкой.

ДВЕ ОСИ, НЕ ОДНА. `our_action` (подали ли МЫ) и `lot_result` (чем кончился
ЛОТ) — независимы: лот выигран конкурентом ровно так же, когда мы подавали и
когда не подавали. Схлопнуть их в один enum значит потерять именно ту
разницу, ради которой всё заводится.

ОТКУДА БЕРЁТСЯ ИСХОД:
  • автоматически — из УЖЕ СОБРАННЫХ нами фидов площадки. Замер 20.08 по 361
    алерченному etender-лоту: 177 нашлись в «ETender Сделки (победители)»,
    91 — в «ETender Несостоявшиеся (лиды)». 74% исхода лежало в нашей же базе
    несшитым, потому что победитель приходит в extra_info->>'Победитель', а не
    в колонку winner;
  • от человека — кнопкой. Для остальных ~95% алертов (Cooperation, XT-Xarid,
    Telegram-каналы) реестра результатов не существует в природе, и других
    источников истины, кроме Данияра, нет.

Человек ВСЕГДА главнее авто: см. merge_result. Обратное затирало бы ручную
правку при каждом ночном прогоне — и молча.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "alert_outcome"

# Фиды площадки, из которых берётся исход. Ключ сшивки — external_id фида,
# он же trade_id, он же хвост /lot/{id} в source_url алерченной строки.
DEALS_SOURCE = "ETender Сделки (победители)"
NOTDEALED_SOURCE = "ETender Несостоявшиеся (лиды)"

# Наши собственные имена. Один список на два вопроса: «мы разместили этот лот»
# (notifier._is_own_lot — подавить) и «мы выиграли этот лот» (здесь — засчитать).
# Это одна и та же идентичность, и расхождение двух копий означало бы, что
# выигранный нами лот считается чужим.
OWN_ORG_FRAGMENTS = frozenset({
    "winch", "винч", "салахутдинов д.у", "salakhutdinov d.u",
})

_LOT_RE = re.compile(r"/lot/(\d+)")

# Разрешённые значения — те же, что в CHECK-ограничениях миграции 023.
ACTIONS = ("bid", "passed")
RESULTS = ("won_by_us", "won_by_other", "no_deal")


# ── чистая логика (тестируется без БД и без сети) ────────────────────────────

def lot_key_from_url(url):
    # type: (Optional[str]) -> Optional[str]
    """id лота из ссылки: https://etender.uzex.uz/lot/508273 -> '508273'.

    Ключ намеренно берётся из URL, а не из external_id: у одних источников
    external_id короткий ('506596'), у других длинный ('26120012508273') —
    ссылка же построена по одному шаблону во всех etender-фидах.
    """
    if not url:
        return None
    m = _LOT_RE.search(str(url))
    return m.group(1) if m else None


def is_our_win(winner):
    # type: (Optional[str]) -> bool
    """Мы ли победитель. Сверяется по тому же списку, что и own-lot в notifier."""
    if not winner:
        return False
    norm = " ".join(str(winner).casefold().split())
    return any(frag in norm for frag in OWN_ORG_FRAGMENTS)


def winner_from_extra(extra_info):
    # type: (Any) -> Optional[str]
    """Победитель из extra_info фида сделок.

    Фид кладёт его строкой «ООО "X" (ИНН 123)» в ключ 'Победитель'. Пустая
    строка и заглушка вида '(ИНН )' — это ОТСУТСТВИЕ победителя, а не имя:
    сделка бывает зарегистрирована без раскрытия поставщика.
    """
    if not isinstance(extra_info, dict):
        return None
    raw = extra_info.get("Победитель")
    if raw is None:
        return None
    val = str(raw).strip()
    if not val:
        return None
    # «(ИНН )» / «None (ИНН None)» — шаблон подставился на пустых полях.
    # На 20.08 таких строк в фиде НЕТ (проверено: 0 из 3855) — защита стоит
    # заранее, потому что появление одной такой строки означало бы поставщика
    # по имени «None», а не отсутствие данных.
    bare = val.replace("(", " ").replace(")", " ").replace("ИНН", " ")
    bare = " ".join(bare.split()).lower()
    if not bare or all(w == "none" for w in bare.split()):
        return None
    return val


def participants_from_extra(extra_info):
    # type: (Any) -> Optional[int]
    """Число участников из extra_info; не число -> None, а не 0."""
    if not isinstance(extra_info, dict):
        return None
    raw = extra_info.get("Участников")
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return None


def classify_deal(extra_info, price=None):
    # type: (Any, Optional[float]) -> Optional[Dict[str, Any]]
    """Строка фида сделок -> исход лота. None, если победителя в ней нет.

    Сделка без раскрытого победителя НЕ считается 'no_deal': торги состоялись,
    мы просто не видим кто выиграл. Соврать здесь значит записать себе в актив
    несуществующее знание.
    """
    winner = winner_from_extra(extra_info)
    if not winner:
        return None
    return {
        "lot_result": "won_by_us" if is_our_win(winner) else "won_by_other",
        "winner": winner,
        "result_price": price,
        "participants": participants_from_extra(extra_info),
        "result_source": "auto:etender-deals",
    }


def merge_result(existing, incoming):
    # type: (Optional[Dict[str, Any]], Dict[str, Any]) -> Optional[Dict[str, Any]]
    """Что записать поверх уже имеющейся строки. None = писать нечего.

    Правила, в порядке приоритета:
      1. Ручной исход (result_source='button') автоматика НЕ трогает никогда.
         Иначе ночной прогон молча стирал бы правку Данияра.
      2. Тот же исход из того же источника — не писать (иначе updated_at
         дёргается каждую ночь и «свежесть» перестаёт что-либо значить).
      3. Во всех остальных случаях — писать.
    """
    if not incoming or not incoming.get("lot_result"):
        return None
    if not existing:
        return dict(incoming)
    if (existing.get("result_source") or "").startswith("button"):
        return None
    same = (existing.get("lot_result") == incoming.get("lot_result")
            and existing.get("result_source") == incoming.get("result_source")
            and existing.get("winner") == incoming.get("winner"))
    if same:
        return None
    return dict(incoming)


def funnel(rows):
    # type: (List[Dict[str, Any]]) -> Dict[str, int]
    """Свод по строкам alert_outcome. Считает ЯВНО известное, не додумывая.

    'исход неизвестен' — полноправная категория и обязана быть видна: отчёт,
    где её нет, читается как «мы всё знаем», а мы не знаем.
    """
    out = {"rows": len(rows), "bid": 0, "passed": 0, "action_unknown": 0,
           "won_by_us": 0, "won_by_other": 0, "no_deal": 0, "result_unknown": 0,
           "bid_and_won": 0, "bid_result_unknown": 0}
    for r in rows:
        act = r.get("our_action")
        res = r.get("lot_result")
        if act == "bid":
            out["bid"] += 1
        elif act == "passed":
            out["passed"] += 1
        else:
            out["action_unknown"] += 1
        if res in RESULTS:
            out[res] += 1
        else:
            out["result_unknown"] += 1
        if act == "bid":
            if res == "won_by_us":
                out["bid_and_won"] += 1
            elif res is None:
                out["bid_result_unknown"] += 1
    return out


# ── запись и чтение (нужна БД) ───────────────────────────────────────────────

def _client():  # type: ignore[no-untyped-def]
    from crawler.core.db import _get_client
    return _get_client()


def record_action(alert_seq, action, tender_id=None, lot_key=None):
    # type: (int, str, Optional[str], Optional[str]) -> bool
    """Записать НАШЕ действие по алерту (кнопка). Идемпотентно."""
    if action not in ACTIONS:
        logger.warning("[Outcome] unknown action %r", action)
        return False
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    row = {"alert_seq": int(alert_seq), "our_action": action,
           "action_at": now, "updated_at": now}
    if tender_id:
        row["tender_id"] = tender_id
    if lot_key:
        row["lot_key"] = lot_key
    try:
        _client().table(TABLE).upsert(row, on_conflict="alert_seq").execute()
        return True
    except Exception as exc:
        logger.error("[Outcome] record_action #%s failed: %s", alert_seq, str(exc)[:150])
        return False


def record_result(alert_seq, lot_result, winner=None, note=None):
    # type: (int, str, Optional[str], Optional[str]) -> bool
    """Записать исход ЛОТА со слов человека. Помечается source='button' и
    после этого автоматикой не перетирается (merge_result правило 1)."""
    if lot_result not in RESULTS:
        logger.warning("[Outcome] unknown lot_result %r", lot_result)
        return False
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    row = {"alert_seq": int(alert_seq), "lot_result": lot_result,
           "result_source": "button", "result_at": now, "updated_at": now}
    if winner:
        row["winner"] = winner
    if note:
        row["note"] = note
    try:
        _client().table(TABLE).upsert(row, on_conflict="alert_seq").execute()
        return True
    except Exception as exc:
        logger.error("[Outcome] record_result #%s failed: %s", alert_seq, str(exc)[:150])
        return False


# PostgREST отдаёт максимум 1000 строк на запрос, СКОЛЬКО БЫ ни просил limit,
# и делает это молча. 20.08 первый же отчёт из-за этого показал 1000 алертов
# вместо 7553 и «исход неизвестен 98%» вместо честной картины — то есть
# усечение выглядело как результат. Ровно от этого уже уходили в healthcheck
# (10.08, кап 200 тыс.), и повторили в другом месте.
#
# Идём keyset'ом по alert_seq вниз, а не offset'ом: под alert_seq есть
# уникальный индекс, а глубокие offset'ы на 870-тысячной таблице — это тот
# самый 57014.
_PAGE = 1000


def iter_by_seq(build_query, page=_PAGE, runner=None):
    # type: (Any, int, Any) -> List[Dict[str, Any]]
    """Все строки запроса, страницами по убыванию alert_seq.

    build_query(last_seq) должен вернуть готовый запрос; last_seq=None на
    первой странице. Полнота выборки здесь важнее скорости: неполный список
    алертов молча превращается в заниженный знаменатель.

    `runner` — как выполнять запрос; по умолчанию db.query_with_retry (защита
    от 57014). Параметром он стал, чтобы тесты не зависели от того, лежит ли
    в sys.modules настоящий crawler.core.db: соседние файлы законно подменяют
    его заглушкой, и без явной передачи чистый тест пагинации падал бы в общем
    прогоне по чужой причине.
    """
    if runner is None:
        from crawler.core.db import query_with_retry as runner  # noqa: F811
    out = []  # type: List[Dict[str, Any]]
    last = None  # type: Optional[int]
    while True:
        res = runner(lambda l=last: build_query(l).limit(page).execute(),
                     label="outcome.page")
        rows = list(getattr(res, "data", None) or [])
        out.extend(rows)
        if len(rows) < page:
            return out
        try:
            last = int(rows[-1]["alert_seq"])
        except (KeyError, TypeError, ValueError):
            logger.error("[Outcome] пагинация без alert_seq — выборка неполна")
            return out


def _alerted_lots(client):
    # type: (Any) -> List[Dict[str, Any]]
    """Алерченные строки, у которых ссылка ведёт на лот площадки.

    Фильтр по source_url делается НА СЕРВЕРЕ: тянуть 7,5 тыс. алертов, чтобы
    оставить 361, — ровно тот способ упереться в 57014, от которого уже
    уходили в healthcheck (10.08).
    """
    def build(last):
        q = (client.table("tenders")
             .select("alert_seq,id,source,source_url,title,price,created_at")
             .not_.is_("alert_seq", "null")
             .like("source_url", "%/lot/%"))
        if last is not None:
            q = q.lt("alert_seq", last)
        return q.order("alert_seq", desc=True)

    return iter_by_seq(build)


def _feed_rows(client, source, keys):
    # type: (Any, str, List[str]) -> Dict[str, Dict[str, Any]]
    """Строки фида по списку id лотов, пачками (URL запроса не резиновый)."""
    from crawler.core.db import query_with_retry
    out = {}  # type: Dict[str, Dict[str, Any]]
    step = 150
    for i in range(0, len(keys), step):
        chunk = keys[i:i + step]
        try:
            res = query_with_retry(
                lambda c=chunk: (client.table("tenders")
                                 .select("external_id,extra_info,price")
                                 .eq("source", source)
                                 .in_("external_id", c)
                                 .execute()),
                label="outcome.feed")
            for row in (getattr(res, "data", None) or []):
                out[str(row.get("external_id"))] = row
        except Exception as exc:
            logger.error("[Outcome] feed %s chunk %d failed: %s", source, i, str(exc)[:150])
    return out


def sync_auto(dry_run=False):
    # type: (bool) -> Dict[str, int]
    """Сшить алерченные лоты с уже собранными фидами площадки.

    Ничего не выкачивает: обе стороны давно лежат в `tenders`. Возвращает
    счётчики; при dry_run пишет только в лог.
    """
    client = _client()
    alerted = _alerted_lots(client)
    keyed = []  # type: List[Dict[str, Any]]
    for row in alerted:
        key = lot_key_from_url(row.get("source_url"))
        if key:
            row["_lot_key"] = key
            keyed.append(row)

    keys = sorted({r["_lot_key"] for r in keyed})
    deals = _feed_rows(client, DEALS_SOURCE, keys)
    notdealed = _feed_rows(client, NOTDEALED_SOURCE, keys)

    existing = {}  # type: Dict[int, Dict[str, Any]]
    for row in load_all():
        try:
            existing[int(row["alert_seq"])] = row
        except (KeyError, TypeError, ValueError):
            continue

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    stats = {"alerted_with_lot": len(keyed), "matched_deal": 0,
             "matched_no_deal": 0, "written": 0, "unchanged": 0, "unmatched": 0}
    writes = []  # type: List[Dict[str, Any]]

    for row in keyed:
        key = row["_lot_key"]
        seq = int(row["alert_seq"])
        incoming = None  # type: Optional[Dict[str, Any]]
        deal = deals.get(key)
        if deal:
            incoming = classify_deal(deal.get("extra_info"), deal.get("price"))
            if incoming:
                stats["matched_deal"] += 1
        if incoming is None and key in notdealed:
            incoming = {"lot_result": "no_deal", "winner": None,
                        "result_price": None, "participants": None,
                        "result_source": "auto:etender-notdealed"}
            stats["matched_no_deal"] += 1
        if incoming is None:
            stats["unmatched"] += 1
            continue

        merged = merge_result(existing.get(seq), incoming)
        if merged is None:
            stats["unchanged"] += 1
            continue
        merged.update({"alert_seq": seq, "tender_id": row.get("id"),
                       "lot_key": key, "result_at": now, "updated_at": now})
        writes.append(merged)

    if dry_run:
        for w in writes[:10]:
            logger.info("[Outcome] DRY #%s %s %s", w["alert_seq"],
                        w["lot_result"], (w.get("winner") or "")[:40])
        stats["written"] = len(writes)
        return stats

    for i in range(0, len(writes), 200):
        batch = writes[i:i + 200]
        try:
            client.table(TABLE).upsert(batch, on_conflict="alert_seq").execute()
            stats["written"] += len(batch)
        except Exception as exc:
            logger.error("[Outcome] upsert batch %d failed: %s", i, str(exc)[:150])
    return stats


def load_all():
    # type: () -> List[Dict[str, Any]]
    """Все строки исходов (таблица маленькая — по одной на алерт)."""
    client = _client()

    def build(last):
        q = client.table(TABLE).select("*")
        if last is not None:
            q = q.lt("alert_seq", last)
        return q.order("alert_seq", desc=True)

    try:
        return iter_by_seq(build)
    except Exception as exc:
        logger.error("[Outcome] load_all failed: %s", str(exc)[:150])
        return []


def pending_confirmations(limit=10):
    # type: (int) -> List[Dict[str, Any]]
    """Лоты, на которые мы подали, а исход так и неизвестен — их и спрашиваем."""
    try:
        res = (_client().table(TABLE).select("*")
               .eq("our_action", "bid")
               .is_("lot_result", "null")
               .order("action_at", desc=False)
               .limit(limit).execute())
        return list(getattr(res, "data", None) or [])
    except Exception as exc:
        logger.error("[Outcome] pending failed: %s", str(exc)[:150])
        return []
