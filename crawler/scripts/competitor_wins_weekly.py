"""competitor_wins_weekly — разбор побед конкурентов раз в 3 дня в Telegram.

(Имя осталось от недельного ритма: под него завязаны крон, логи и курсор.)

Что забрали чужие по нашему профилю и где в этот момент были мы: алертили /
алерта не было (и почему) / лот не собирали; плюс «спрятанные» — победы
конкурентов на лотах, которых гейт не узнал; плюс блок по списку конкурентов
(config/competitor_registry.json). Победителей берём из двух фидов: сделки etender
и итоги отбора ВМК-69. Логика и мотивация — crawler/core/competitor_wins.py,
здесь только ввод-вывод.

Usage:
  python3 -m crawler.scripts.competitor_wins_weekly --dry-run          # печать, ничего не пишет
  python3 -m crawler.scripts.competitor_wins_weekly --tg               # отправка + сдвиг курсора
  python3 -m crawler.scripts.competitor_wins_weekly --dry-run --days 30   # замер за период
  --due     пора ли слать: exit 0 — пора, 10 — рано (ничего не печатает и не пишет)
  --monitor-receipts DIR  каталог квитанций монитора площадок: в сводку идёт строка о нём
  --no-ai   без AI: где нужен AI, победа остаётся нерешённой (для отладки)
  --force   разрешить повторную отправку раньше срока / --tg вместе с --days

Крон: ежедневно 05:00 UTC через scripts/run_competitor_digest.sh (там `--due`
решает, пора ли: раз в 3 дня). Курсор двигается ТОЛЬКО после доставки; нерешённые
(потолок или сбой AI) переносятся в следующий разбор, а не пропадают. Ничего,
кроме курсора, скрипт не пишет.
"""
import os

# До любого crawler-импорта: replay/ai_decision_log читают путь лога при импорте,
# и трафик разбора не должен смешиваться с боевым журналом решений AI.
os.environ.setdefault("PARSING_AI_LOG", "/tmp/competitor-wins-ai-decisions.jsonl")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Tuple  # noqa: E402

import httpx  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.config.settings import settings  # noqa: E402
from crawler.core import competitor_wins as CW  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("competitor_wins")

AI_CAP = 60          # потолок вызовов AI за прогон; остальное — в retry, а не в тишину
EXIT_NOT_DUE = 10    # --due: ритм ещё не вышел
PAGE = 500
URL_BATCH = 40       # source_url в одном IN — URL запроса не резиновый
ID_BATCH = 100
SEQ_BATCH = 200
FEED_WEEKS = 4       # сколько прошлых окон берём для «обычного» числа сделок

_DEAL_FIELDS = ("id,external_id,source,source_url,title,organization,search_text,price,currency,"
                "deadline,message_type,extra_info,bid_count,status,collected_at,created_at")
_CIVIL_FIELDS = ("id,external_id,source,source_url,title,organization,price,winning_price,currency,"
                 "winner,status,result_date,message_type,created_at")
_LOT_FIELDS = _DEAL_FIELDS + ",alert_seq,telegram_message_id,relevance_score,relevance_category"
MONITOR_MAX_AGE_H = 6   # квитанция монитора старше — не «сегодняшняя»
CIVIL_KEY_BATCH = 100


def _client():  # type: ignore[no-untyped-def]
    from crawler.core.db import _get_client
    return _get_client()


def _run(fn, label):
    # type: (Callable[[], Any], str) -> Any
    from crawler.core.db import query_with_retry
    return query_with_retry(fn, label=label)


def _rows(fn, label):
    # type: (Callable[[], Any], str) -> List[Dict[str, Any]]
    rows = _run(fn, label).data or []
    if len(rows) >= 1000:
        raise RuntimeError("%s упёрся в потолок PostgREST (1000) — выборка неполна" % label)
    return rows


# ── чтение ───────────────────────────────────────────────────────────────────

def fetch_feed(c, source, fields, start, end):
    # type: (Any, str, str, datetime, datetime) -> List[Dict[str, Any]]
    """Строки фида, впервые увиденные в [start, end). Keyset по (created_at, id)."""
    out = []  # type: List[Dict[str, Any]]
    last = None  # type: Optional[Tuple[str, str]]
    while True:
        def build(last=last):
            q = (c.table("tenders").select(fields)
                 .eq("source", source)
                 .gte("created_at", start.isoformat()).lt("created_at", end.isoformat()))
            if last:
                q = q.or_(CW.keyset_filter(last[0], last[1]))
            return q.order("created_at").order("id").limit(PAGE).execute()
        rows = _run(build, "cw-feed").data or []
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        last = (rows[-1]["created_at"], rows[-1]["id"])


def fetch_deals(c, start, end):
    # type: (Any, datetime, datetime) -> List[Dict[str, Any]]
    return fetch_feed(c, CW.DEALS_SOURCE, _DEAL_FIELDS, start, end)


def fetch_civil(c, start, end):
    # type: (Any, datetime, datetime) -> List[Dict[str, Any]]
    return fetch_feed(c, CW.CIVIL_SOURCE, _CIVIL_FIELDS, start, end)


def fetch_deals_by_id(c, ids):
    # type: (Any, List[str]) -> List[Dict[str, Any]]
    """Нерешённые прошлого разбора: оба фида (id — uuid, между источниками не пересекаются)."""
    out = []  # type: List[Dict[str, Any]]
    for i in range(0, len(ids), ID_BATCH):
        batch = ids[i:i + ID_BATCH]

        def build(batch=batch):
            return (c.table("tenders").select(_DEAL_FIELDS + ",winning_price,winner,status,result_date")
                    .in_("source", [CW.DEALS_SOURCE, CW.CIVIL_SOURCE]).in_("id", batch).execute())
        out.extend(_rows(build, "cw-retry"))
    return out


def count_deals(c, start, end, source=CW.DEALS_SOURCE):
    # type: (Any, datetime, datetime, str) -> int
    def build():
        return (c.table("tenders").select("id", count="exact")
                .eq("source", source)
                .gte("created_at", start.isoformat()).lt("created_at", end.isoformat())
                .limit(1).execute())
    return int(_run(build, "cw-count").count or 0)


def fetch_lot_rows(c, urls):
    # type: (Any, List[str]) -> Dict[str, List[Dict[str, Any]]]
    by_url = {}  # type: Dict[str, List[Dict[str, Any]]]
    for i in range(0, len(urls), URL_BATCH):
        batch = urls[i:i + URL_BATCH]

        def build(batch=batch):
            return (c.table("tenders").select(_LOT_FIELDS)
                    .in_("source", list(CW.LOT_SOURCES)).in_("source_url", batch).execute())
        for r in _rows(build, "cw-lots"):
            by_url.setdefault(r.get("source_url") or "", []).append(r)
    return by_url


def fetch_civil_lot_rows(c, keys):
    # type: (Any, List[str]) -> Dict[str, List[Dict[str, Any]]]
    """Наши строки лотов ВМК-69 по display_id (external_id лота = display_id итога).

    Ищем все написания номера и раскладываем по civil_norm_key: лот, собранный до
    смены формата id, и итог после неё — одна процедура.
    """
    ids = sorted({v for k in keys for v in CW.civil_id_variants(k)})
    by_key = {}  # type: Dict[str, List[Dict[str, Any]]]
    for i in range(0, len(ids), CIVIL_KEY_BATCH):
        batch = ids[i:i + CIVIL_KEY_BATCH]

        def build(batch=batch):
            return (c.table("tenders").select(_LOT_FIELDS)
                    .in_("source", list(CW.CIVIL_LOT_SOURCES)).in_("external_id", batch).execute())
        for r in _rows(build, "cw-civil-lots"):
            by_key.setdefault(CW.civil_norm_key(r.get("external_id")), []).append(r)
    return by_key


def civil_seen_before(c, rows, start):
    # type: (Any, List[Dict[str, Any]], datetime) -> set
    """civil_norm_key итогов окна, которые база уже знала ДО окна под другим написанием id.

    Так выглядит смена формата: 24.09 API переписал display_id, и results_tracker
    вставил 454 старых итога новыми строками с сегодняшним created_at. Без этой
    проверки сводка показала бы их как свежие победы.
    """
    pairs = [(CW.civil_norm_key(r.get("external_id")),
              [CW.CIVIL_ID_PREFIX + v for v in CW.civil_id_variants(r.get("external_id"))
               if CW.CIVIL_ID_PREFIX + v != r.get("external_id")]) for r in rows]
    ids = sorted({x for _, alts in pairs for x in alts})
    known = set()  # type: set
    for i in range(0, len(ids), CIVIL_KEY_BATCH):
        batch = ids[i:i + CIVIL_KEY_BATCH]

        def build(batch=batch):
            return (c.table("tenders").select("external_id").eq("source", CW.CIVIL_SOURCE)
                    .in_("external_id", batch).lt("created_at", start.isoformat()).execute())
        known |= {CW.civil_norm_key(r.get("external_id")) for r in _rows(build, "cw-civil-seen")}
    return known


def fetch_human_labels(c, seqs):
    # type: (Any, List[int]) -> Dict[int, str]
    """Последняя метка человека по алерту: {alert_seq: corrected_label}."""
    labels = {}  # type: Dict[int, str]
    for i in range(0, len(seqs), SEQ_BATCH):
        batch = seqs[i:i + SEQ_BATCH]

        def build(batch=batch):
            return (c.table("alert_feedback").select("alert_seq,corrected_label,created_at")
                    .in_("alert_seq", batch).order("created_at").execute())
        for r in _rows(build, "cw-feedback"):
            if r.get("alert_seq") is not None:
                labels[int(r["alert_seq"])] = r.get("corrected_label") or ""
    return labels


def fetch_our_actions(c, seqs):
    # type: (Any, List[int]) -> Dict[int, str]
    actions = {}  # type: Dict[int, str]
    for i in range(0, len(seqs), SEQ_BATCH):
        batch = seqs[i:i + SEQ_BATCH]

        def build(batch=batch):
            return (c.table("alert_outcome").select("alert_seq,our_action")
                    .in_("alert_seq", batch).execute())
        for r in _rows(build, "cw-outcome"):
            if r.get("our_action"):
                actions[int(r["alert_seq"])] = r["our_action"]
    return actions


def read_state(c):
    # type: (Any) -> Optional[Dict[str, Any]]
    """Состояние курсора. Сбой базы — ИСКЛЮЧЕНИЕ, а не None: иначе недоступная
    база молча превращалась бы в «первый запуск» с окном в 7 дней."""
    def build():
        return c.table("crawler_settings").select("value").eq("key", CW.CURSOR_KEY).limit(1).execute()
    rows = _run(build, "cw-cursor").data or []
    if not rows:
        return None
    raw = rows[0].get("value")
    state = json.loads(raw) if isinstance(raw, str) else raw
    return state if isinstance(state, dict) else None


def write_state(state):
    # type: (Dict[str, Any]) -> bool
    from crawler.auth.session_store import session_store
    return session_store.set_setting(CW.CURSOR_KEY, state)


def keyword_hit_fn():
    # type: () -> Callable[[str], bool]
    """Тот же сопоставитель ключевых слов, что у гейта (стем в начале слова)."""
    from crawler.core.notifier import _find_matching_keyword, _get_keywords
    from crawler.core.tender_rows import row_to_raw_tender
    kws = _get_keywords()

    def hit(name):
        return _find_matching_keyword(row_to_raw_tender({"title": name}), kws) is not None
    return hit


# ── суждение гейта ───────────────────────────────────────────────────────────

class _Budget(object):
    def __init__(self, cap):
        self.cap, self.used, self.errors = cap, 0, 0


async def _replay(rows, use_ai):
    # type: (List[Dict[str, Any]], bool) -> List[Any]
    """Прогон через гейт без записи. Лот судится на дату ПЕРВОГО появления
    (created_at): collected_at перезаписывает каждый upsert, и закрытый лот
    умирал бы на «срок истёк» раньше, чем дошёл бы до настоящей причины."""
    from crawler.core.tender_rows import row_to_raw_tender
    from crawler.scripts.replay import replay_tenders
    tenders = [row_to_raw_tender(r) for r in rows]
    first_seen = {t.external_id: r.get("created_at") for t, r in zip(tenders, rows)}
    return await replay_tenders(tenders, use_ai=use_ai, as_of="collected_at", collected_at=first_seen)


async def _ai(row, budget):
    # type: (Dict[str, Any], _Budget) -> Optional[Any]
    """Вердикт с AI или None, если бюджет кончился / AI не ответил."""
    if budget.used >= budget.cap:
        return None
    budget.used += 1
    v = (await _replay([row], use_ai=True))[0]
    if getattr(v, "ai_error", False):
        budget.errors += 1
        return None
    return v


async def judge(entries, use_ai, budget):
    # type: (List[Dict[str, Any]], bool, _Budget) -> None
    """Профиль там, где его не решили клик и сохранённый вердикт. Пишет в сами
    словари: profile (bool), reason, stage, undecided.

    Лот — своей строкой; сделка или итог ВМК-69 (без имени победителя) — только
    когда своей строки лота нет. AI-вердикт по сделке никогда не перекрывает отказ по лоту:
    «Услуги печатные» у площадки прячет и баннеры, и статьи.
    """
    need = [e for e in entries if e.get("profile") is None]
    if not need:
        return
    rows = [e["lot_row"] if e.get("lot_row") else CW.clean_row(e["win"].get("feed"), e["deal_row"])
            for e in need]
    for e, row, v in zip(need, rows, await _replay(rows, use_ai=False)):
        e["gate_row"], e["prefilter"] = row, v

    # Дорогие первыми: потолок AI съедает хвост мелочи, а не крупные лоты.
    for e in sorted(need, key=lambda e: -float(e["win"].get("won_price") or 0)):
        pf = e["prefilter"]
        if not pf.passed_prefilter:
            e["profile"], e["stage"] = False, pf.dropped_at_stage
            continue
        if not use_ai:
            e["undecided"] = True
            continue
        v = await _ai(e["gate_row"], budget)
        if v is None:
            e["undecided"] = True
            continue
        e["profile"] = CW.is_profile_verdict(v)
        e["stage"] = None if e["profile"] else CW.stage_of(v)
        if e["profile"] and e["status"] == CW.STATUS_MISSED:
            e["reason"] = CW.REASON_PASSES_TODAY


# ── сборка отчёта ────────────────────────────────────────────────────────────

def _after_deadline(alert_row):
    # type: (Dict[str, Any]) -> bool
    seen, deadline = CW.parse_ts(alert_row.get("created_at")), CW.parse_ts(alert_row.get("deadline"))
    return bool(seen and deadline and seen > deadline)


async def build_report(start, end, clamped, retry_ids=(), use_ai=True):
    # type: (datetime, datetime, bool, Any, bool) -> Tuple[Dict[str, Any], List[str], List[str]]
    """(отчёт, ИНН реестра, id нерешённых сделок для retry)."""
    from crawler.core.competitor_audit import load_registry, registry_inns
    from crawler.core.notifier import MIN_PRICE  # тот же порог, что у гейта

    c = _client()
    deals = fetch_deals(c, start, end)
    civil_raw = fetch_civil(c, start, end)
    # Здоровье фида считаем по сырым строкам — так же, как count_deals в прошлых окнах.
    window_count, civil_count = len(deals), len(civil_raw)
    civil = CW.dedupe_civil(civil_raw)
    known = civil_seen_before(c, civil, start)
    civil = [r for r in civil if CW.civil_norm_key(r.get("external_id")) not in known]
    seen_ids = {d["id"] for d in deals} | {d["id"] for d in civil_raw}
    retry = [x for x in retry_ids if x and x not in seen_ids]
    retried = fetch_deals_by_id(c, retry) if retry else []
    span = end - start
    previous = [count_deals(c, start - span * k, end - span * k) for k in range(1, FEED_WEEKS + 1)]
    median, dropped = CW.feed_health(window_count, previous)
    previous_civil = [count_deals(c, start - span * k, end - span * k, CW.CIVIL_SOURCE)
                      for k in range(1, FEED_WEEKS + 1)]
    civil_median, civil_dropped = CW.feed_health(civil_count, previous_civil)
    cov = {"deals": window_count, "deals_median": int(round(median)) if median else None,
           "feed_dropped": dropped, "civil": civil_count,
           "civil_median": int(round(civil_median)) if civil_median else None,
           "civil_dropped": civil_dropped, "civil_repeats": civil_count - len(civil),
           "hidden_winner": 0, "unlinked": 0, "not_profile": 0,
           "human_rejected": 0, "ai_used": 0, "ai_cap": AI_CAP if use_ai else 0,
           "ai_errors": 0, "undecided": 0, "retried": len(retry)}

    wins, ours = [], []
    feed_rows = ([(CW.parse_win, r) for r in deals] + [(CW.parse_civil_win, r) for r in civil]
                 + [(CW.parse_civil_win if r.get("source") == CW.CIVIL_SOURCE else CW.parse_win, r)
                    for r in retried])
    for parse, row in feed_rows:
        w = parse(row)
        if w is None:
            cov["hidden_winner"] += 1
        elif w["ours"]:
            ours.append(w)
        elif not w.get("lot_key"):
            cov["unlinked"] += 1        # без /lot/ не сшить — и не врать «не собирали»
        else:
            wins.append((w, row))

    lots_by_url = fetch_lot_rows(c, sorted({w["source_url"] for w, _ in wins
                                            if w["feed"] == CW.FEED_DEALS}))
    civil_lots = fetch_civil_lot_rows(c, sorted({w["lot_key"] for w, _ in wins
                                                 if w["feed"] == CW.FEED_CIVIL}))

    def lots_of(w):
        if w["feed"] == CW.FEED_CIVIL:
            return civil_lots.get(CW.civil_norm_key(w["lot_key"]), [])
        return lots_by_url.get(w["source_url"], [])

    seqs = sorted({int(r["alert_seq"]) for w, _ in wins for r in lots_of(w)
                   if r.get("alert_seq") is not None})
    labels = fetch_human_labels(c, seqs) if seqs else {}
    actions = fetch_our_actions(c, seqs) if seqs else {}

    entries = []
    for w, row in wins:
        lot_rows = lots_of(w)
        status, first = CW.alert_status(lot_rows, labels)
        if status == CW.STATUS_REJECTED_BY_HUMAN:
            cov["human_rejected"] += 1
            continue
        e = {"win": w, "status": status, "deal_row": row, "stage": None, "reason": None,
             "ai_then": CW.stored_ai_then(lot_rows)}
        if status == CW.STATUS_ALERTED:
            alerted = [r for r in lot_rows if r.get("alert_seq") is not None]
            e.update(first_alert=first, our_action=actions.get(int(first["alert_seq"])),
                     lot_row=CW.pick_lot_row(alerted), alert_after_deadline=_after_deadline(first),
                     profile=CW.alert_profile(alerted, labels))
        elif status == CW.STATUS_MISSED:
            loss = CW.missed_is_delivery_loss(lot_rows)
            e.update(lot_row=CW.pick_lot_row(lot_rows), profile=True if loss else None,
                     reason=CW.REASON_DELIVERY_LOSS if loss else None)
        else:
            e.update(lot_row=None, profile=None)
        entries.append(e)

    budget = _Budget(AI_CAP if use_ai else 0)
    await judge(entries, use_ai, budget)
    cov["ai_used"], cov["ai_errors"] = budget.used, budget.errors

    keep = ("win", "status", "stage", "reason", "ai_then", "first_alert", "our_action",
            "alert_after_deadline")
    items, rejected, undecided = [], [], []
    for e in entries:
        if e.get("undecided"):
            undecided.append(e["win"]["deal_id"])
        elif e.get("profile"):
            items.append({k: e.get(k) for k in keep})
        else:
            cov["not_profile"] += 1
            rejected.append(e)
    cov["undecided"] = len(undecided)

    # Типографии, выигравшие то, чего гейт не узнал: единственный сигнал, не
    # зависящий от гейта, — поэтому именно отсюда видны дыры словаря.
    registry = [str(x) for x in registry_inns(load_registry())]
    printer_inns = set(registry) | {it["win"]["winner_inn"] for it in items if it["win"].get("winner_inn")}
    hit = keyword_hit_fn()
    printers = []
    for e in rejected:
        w = e["win"]
        if (CW.is_uzs(w.get("currency")) and float(w.get("won_price") or 0) >= MIN_PRICE
                and CW.printer_like(w, printer_inns, hit)):
            item = {k: e.get(k) for k in keep}
            item["lot_status"], item["status"] = e["status"], CW.PRINTER
            printers.append(item)

    report = {"start": start, "end": end, "clamped": clamped, "items": items,
              "printers": printers, "ours": ours, "coverage": cov}
    return report, registry, undecided


# ── отправка ─────────────────────────────────────────────────────────────────

async def _post(payload):
    # type: (Dict[str, Any]) -> Tuple[int, str]
    url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        resp = await client.post(url, json=payload)
    return resp.status_code, resp.text[:250]


async def send(text):
    # type: (str) -> bool
    """HTML; на 400 — повтор простым текстом: одна кривая сущность иначе
    блокировала бы доставку каждую неделю, и разбор снова молчал бы."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.error("нет telegram-конфига — не отправляю")
        return False
    base = {"chat_id": settings.telegram_alert_chat_id, "disable_web_page_preview": True}
    try:
        code, body = await _post(dict(base, text=text, parse_mode="HTML"))
        if code == 400:
            logger.warning("HTML отвергнут (%s) — шлю простым текстом", body)
            code, body = await _post(dict(base, text=CW.plain_text(text)))
    except Exception as exc:
        logger.error("telegram упал: %s", str(exc)[:150])
        return False
    if code != 200:
        logger.error("telegram %d: %s", code, body)
        return False
    return True


def read_monitor(directory, now):
    # type: (str, datetime) -> Dict[str, Any]
    """Свежая квитанция монитора площадок → итог для сводки. Нет файла, протух или
    не читается — {"error": …}: сводка скажет об этом, а не промолчит."""
    paths = sorted(glob.glob(os.path.join(directory, "*-delta.json")), key=os.path.getmtime)
    if not paths:
        return {"error": "квитанции в %s нет" % directory}
    path = paths[-1]
    age_h = (now.timestamp() - os.path.getmtime(path)) / 3600.0
    if age_h > MONITOR_MAX_AGE_H:
        return {"error": "последняя квитанция %s старше %d ч" % (os.path.basename(path), MONITOR_MAX_AGE_H)}
    from crawler.scripts.monitor_competitor_awards import _REPORTED_STATUSES
    try:
        with open(path, encoding="utf-8") as fh:
            return CW.summarize_monitor(json.load(fh), _REPORTED_STATUSES)
    except Exception as exc:
        return {"error": "квитанция %s не читается: %s" % (os.path.basename(path), str(exc)[:80])}


async def run(args):
    # type: (argparse.Namespace) -> int
    now = datetime.now(timezone.utc)
    state = read_state(_client())
    if args.due:
        if not CW.is_due(state, now):
            logger.info("рано: %s", CW.repeat_blocked(state, now))
            return EXIT_NOT_DUE
        logger.info("пора: последняя доставка %s", (state or {}).get("delivered_at") or "—")
        return 0
    if args.tg and not args.force:
        if args.days:
            logger.error("--tg с --days перешлёт победы, которые покажет и понедельник; нужен --force")
            return 2
        blocked = CW.repeat_blocked(state, now)
        if blocked:
            logger.error("%s — повтор только с --force", blocked)
            return 2
    if args.days:
        end = now - CW.SAFETY_LAG
        start, clamped = end - timedelta(days=args.days), False
        retry_ids = []  # type: List[str]
    else:
        start, end, clamped = CW.window((state or {}).get("cursor"), now)
        retry_ids = list((state or {}).get("retry") or [])
    logger.info("окно %s — %s%s; retry %d", start.isoformat(), end.isoformat(),
                " (прижато)" if clamped else "", len(retry_ids))
    report, registry, undecided = await build_report(start, end, clamped, retry_ids, use_ai=not args.no_ai)
    from crawler.core.competitor_audit import load_registry
    monitor = read_monitor(args.monitor_receipts, now) if args.monitor_receipts else None
    text = CW.build_message(report, registry, watch=CW.watch_map(load_registry()), monitor=monitor)
    print(text)
    logger.info("профиль %d · типографий вне профиля %d · покрытие %s", len(report["items"]),
                len(report["printers"]), json.dumps(report["coverage"], ensure_ascii=False))
    if not args.tg:
        return 0
    if not await send(text):
        logger.error("разбор не доставлен — курсор не сдвинут, следующий запуск покажет и это окно")
        return 1
    if args.days:
        logger.info("доставлено; окно задано вручную — курсор не трогаю")
        return 0
    if not write_state(CW.next_state(end, len(report["items"]), undecided, now)):
        logger.error("доставлено, но курсор не записан — следующий разбор повторит это окно")
        return 1
    logger.info("доставлено, курсор → %s, retry %d", end.isoformat(), len(undecided))
    return 0


def main():
    # type: () -> int
    ap = argparse.ArgumentParser(description="Разбор побед конкурентов раз в 3 дня")
    ap.add_argument("--tg", action="store_true", help="отправить в алерт-канал и сдвинуть курсор")
    ap.add_argument("--dry-run", action="store_true", help="только печать (по умолчанию без --tg)")
    ap.add_argument("--days", type=int, default=0, help="окно вручную, 1–60 дней (курсор не трогается)")
    ap.add_argument("--due", action="store_true", help="пора ли слать: exit 0 — пора, 10 — рано")
    ap.add_argument("--monitor-receipts", default="",
                    help="каталог квитанций монитора площадок (*-delta.json) — строка о нём в сводке")
    ap.add_argument("--no-ai", action="store_true", help="без AI: где он нужен — «не решено»")
    ap.add_argument("--force", action="store_true", help="повторная отправка / --tg вместе с --days")
    args = ap.parse_args()
    if args.days and not 1 <= args.days <= 60:
        ap.error("--days: от 1 до 60")
    if args.dry_run and args.tg:
        ap.error("--dry-run и --tg взаимоисключающие")
    if args.due and (args.tg or args.dry_run or args.days):
        ap.error("--due — отдельный режим: только ответ «пора / рано»")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
