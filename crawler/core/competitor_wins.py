"""Недельный разбор побед конкурентов: что забрали и где в этот момент были мы.

ЗАЧЕМ (01.10.2026). Реестровый монитор (`monitor_competitor_awards`) считает
конкурентом 18 ИНН из ручного списка. За 30 дней в etender реестр видел 5 побед
нашего профиля (все — одной фирмы), остальные забрали типографии вне списка.
Первый штатный прогон 28.09 нашёл «0 новых» и промолчал: «никто ничего не
выиграл» и «сломалось» стали неотличимы. И он не знает, где в этот момент были
МЫ — а там главное: 512255 (журнал, 870 млн) мы алертили дважды, ни одного
клика, выиграл OLTIN-NASHR за 620 при 4 участниках.

Здесь конкурент — любой, кто выиграл лот НАШЕГО профиля, и каждая победа
сшита с нашей историей:
  ALERTED        — лот алертили (пуш или дайджест); заявка не отмечена или «подавали»;
  MISSED         — лот собрали, алерта не было: либо AI тогда сказал «наш» и
                   алерт потерялся по дороге, либо сегодня гейт его пропускает;
  NOT_COLLECTED  — лота в базе нет;
плюс отдельная секция «выиграли типографии — гейт не узнал».

ПРОФИЛЬ РЕШАЕТ НАШ ГЕЙТ, И У ЭТОГО ЕСТЬ СЛЕПОЕ ПЯТНО. Лот с латинским
«Poligrafiya…» (501503) не проходит ключевики ни как лот, ни как сделка — такой
пропуск гейт-определённый профиль не увидит по построению. Видно его только по
победителю: типография по реестру, по нашим же прошлым победам в окне или по
печатному слову в названии (LABEL PRINT, OFSET-SURXON) — `printer_like`.

ИМЯ ПОБЕДИТЕЛЯ НЕ СУДИТ ПРОФИЛЬ ЛОТА. Фид сделок кладёт победителя в
search_text, и «оформление сцены» (511179) проходило ключевики по `ofset` из
имени OFSET-SURXON. Сделку гейт видит только как «предмет + заказчик»
(`clean_deal_row`), и только когда своей строки лота у нас нет.

АЛЕРТ — НЕ ДОКАЗАТЕЛЬСТВО ПРОФИЛЯ. UZEX идёт в обход AI (annotate-not-gate):
теплица и видеонаблюдение ушли алертом с AI-баллом 0. Профиль алерта:
клик человека > сохранённый вердикт гейта > (нет вердикта) сегодняшний AI.

Обучение по кликам в v1 сознательно НЕ пишется: alert_feedback читают few-shot
лидов Telegram, оценка источников, refine_patterns — непоказанный лот с меткой
отравил бы их (ревью плана 01.10). Модуль чистый: ни БД, ни сети; ввод-вывод —
`scripts/competitor_wins_weekly.py`.
"""

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from crawler.core.outcome import (
    DEALS_SOURCE,
    is_our_win,
    lot_key_from_url,
    participants_from_extra,
    winner_from_extra,
)

# Наши строки лотов etender, сшиваемые со сделкой по /lot/{id}. ВМК-69 сюда не
# входит: его ссылки — /civil-detail/, с фидом сделок они не пересекаются.
# Порядок = предпочтение, какую строку гнать через гейт: тендер, обсуждение, лид.
LOT_SOURCES = (
    "ETender UZEX",
    "ETender Обсуждения",
    "ETender Несостоявшиеся (лиды)",
)

CURSOR_KEY = "competitor_wins_cursor_v1"
FIRST_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 21
# Окно кончается с запасом: created_at = начало транзакции upsert идущего
# краула, строка, закоммиченная после чтения, иначе выпала бы навсегда.
SAFETY_LAG = timedelta(minutes=30)
# Повтор раньше трёх дней — почти наверняка дубль (крон дважды, ручной
# перезапуск); неделю не берём, иначе ручная отправка в четверг съедала бы
# штатный понедельник.
MIN_REPEAT = timedelta(days=3)
RETRY_CAP = 200

STATUS_ALERTED = "alerted"
STATUS_MISSED = "missed"
STATUS_NOT_COLLECTED = "not_collected"
STATUS_REJECTED_BY_HUMAN = "rejected_by_human"
PRINTER = "printer"

# Почему профильный лот остался без алерта.
REASON_DELIVERY_LOSS = "delivery_loss"   # AI тогда сказал «наш», алерта нет
REASON_PASSES_TODAY = "passes_today"     # тогда отсёк, сегодня гейт пропускает

UZS_LABELS = ("UZS", "Сум")          # инвариант проекта: одна валюта, разные метки
TELEGRAM_LIMIT = 4096
_TEXT_BUDGET = 3900

# «ИНН: 123» — старый формат итогов ВМК-69 (до 01.10.2026), строки старше окна API
# бэкфилл не перепишет.
_INN_RE = re.compile(r"ИНН:?\s*(\d{9,14})")
_START_PRICE_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(.*)$")
_TAG_RE = re.compile(r"<[^>]+>")

# Человеческие имена стадий гейта (DropStage.* из notifier) + «ai».
STAGE_RU = {
    "message_type": "тип сообщения",
    "no_push_source": "источник без пуша",
    "own_lot": "наш лот",
    "min_price": "цена ниже порога",
    "deadline_expired": "срок истёк",
    "stale": "устарел",
    "no_keyword": "нет ключевого слова",
    "reject_title": "стоп-слово в заголовке",
    "ai": "AI не счёл нашим",
}


# ── разбор строки сделки ─────────────────────────────────────────────────────

def winner_inn(winner):
    # type: (Optional[str]) -> Optional[str]
    """ИНН из «ООО "X" (ИНН 123)». Без ИНН — None: по имени не склеиваем."""
    if not winner:
        return None
    m = _INN_RE.search(str(winner))
    return m.group(1) if m else None


def winner_name(winner):
    # type: (Optional[str]) -> str
    """Имя без хвоста «(ИНН …)» — для показа."""
    name = _INN_RE.sub("", str(winner or "")).replace("()", "").strip(" ,")
    if not name:
        inn = winner_inn(winner)
        return "ИНН %s" % inn if inn else ""    # без имени показываем хотя бы ИНН
    return name


def is_uzs(currency):
    # type: (Optional[str]) -> bool
    return (currency or "").strip() in UZS_LABELS


def parse_start_price(extra_info):
    # type: (Any) -> Tuple[Optional[float], Optional[str]]
    """«870000000.0 Сум» → (870000000.0, 'Сум'). Не число — (None, None)."""
    if not isinstance(extra_info, dict):
        return None, None
    raw = extra_info.get("Цена старт")
    if raw is None:
        return None, None
    m = _START_PRICE_RE.match(str(raw))
    if not m:
        return None, None
    try:
        value = float(m.group(1).replace(",", "."))
    except ValueError:
        return None, None
    return value, (m.group(2) or "").strip() or None


def parse_win(row):
    # type: (Dict[str, Any]) -> Optional[Dict[str, Any]]
    """Строка фида сделок → победа. None — если победитель скрыт.

    Скрытый победитель — не «никто не выиграл»: торги были, мы не видим кого;
    такие сделки идут в строку покрытия, а не в разбор.
    """
    extra = row.get("extra_info") if isinstance(row.get("extra_info"), dict) else {}
    winner = winner_from_extra(extra)
    if not winner:
        return None
    start_price, start_currency = parse_start_price(extra)
    return {
        "deal_id": row.get("id"),
        "lot_key": lot_key_from_url(row.get("source_url")),
        "source_url": row.get("source_url") or "",
        "title": (row.get("title") or "").strip(),
        "customer": (row.get("organization") or "").strip(),
        "won_price": row.get("price"),
        "currency": row.get("currency") or "",
        "start_price": start_price,
        "start_currency": start_currency,
        "participants": participants_from_extra(extra),
        "winner": winner,
        "winner_inn": winner_inn(winner),
        "deal_date": str(extra.get("Дата сделки") or "")[:10] or None,
        "created_at": row.get("created_at"),
        "ours": is_our_win(winner),
    }


def clean_deal_row(row):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Строка сделки для гейта: предмет + заказчик, БЕЗ победителя и extra_info."""
    out = dict(row)
    out["search_text"] = " ".join(
        x for x in ((row.get("title") or "").strip(), (row.get("organization") or "").strip()) if x)
    out["extra_info"] = {}
    return out


def discount_pct(start_price, won_price):
    # type: (Optional[float], Optional[float]) -> Optional[float]
    try:
        start, won = float(start_price), float(won_price)
    except (TypeError, ValueError):
        return None
    if start <= 0 or won <= 0 or won > start:
        return None
    return round((start - won) / start * 100, 1)


# ── где были мы ──────────────────────────────────────────────────────────────

def pick_lot_row(lot_rows):
    # type: (Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]
    """Какую из наших строк лота гнать через гейт: по порядку LOT_SOURCES,
    внутри источника — самую свежую."""
    if not lot_rows:
        return None
    rank = {s: i for i, s in enumerate(LOT_SOURCES)}
    rows = sorted(lot_rows, key=lambda r: str(r.get("collected_at") or ""), reverse=True)
    rows.sort(key=lambda r: rank.get(r.get("source"), len(LOT_SOURCES)))
    return rows[0]


def alert_status(lot_rows, human_labels):
    # type: (Sequence[Dict[str, Any]], Dict[int, str]) -> Tuple[str, Optional[Dict[str, Any]]]
    """(статус, строка первого алерта). human_labels: {alert_seq: метка человека}.

    Лот, у которого ВСЕ алерты человек пометил «мимо»/«реклама», — не наш
    профиль по решению человека.
    """
    alerted = [r for r in lot_rows if r.get("alert_seq") is not None]
    if not alerted:
        return (STATUS_MISSED if lot_rows else STATUS_NOT_COLLECTED), None
    alerted.sort(key=lambda r: int(r["alert_seq"]))
    labels = [human_labels.get(int(r["alert_seq"])) for r in alerted]
    if all(lab in ("ad", "irrelevant") for lab in labels):
        return STATUS_REJECTED_BY_HUMAN, alerted[0]
    return STATUS_ALERTED, alerted[0]


def _verdicts(rows):
    # type: (Iterable[Dict[str, Any]]) -> List[str]
    from crawler.core.feedback import _system_verdict
    return [_system_verdict(r.get("relevance_category"), r.get("relevance_score")) for r in rows]


def alert_profile(alerted_rows, human_labels):
    # type: (Sequence[Dict[str, Any]], Dict[int, str]) -> Optional[bool]
    """Наш ли профиль лот, который мы АЛЕРТИЛИ. None — неизвестно, нужен AI.

    Клик человека «наш» на любом алерте → да; сохранённый вердикт гейта
    'client' → да; все вердикты weak/irrelevant/ad → нет; вердикта нет → None.
    """
    rows = [r for r in alerted_rows if r.get("alert_seq") is not None]
    if any(human_labels.get(int(r["alert_seq"])) == "client" for r in rows):
        return True
    verdicts = _verdicts(rows)
    if any(v == "client" for v in verdicts):
        return True
    if verdicts and all(v in ("weak", "irrelevant", "ad") for v in verdicts):
        return False
    return None


def stored_ai_then(lot_rows):
    # type: (Sequence[Dict[str, Any]]) -> Optional[int]
    """Лучший AI-балл, который гейт сохранил по нашим строкам лота. None —
    до AI лот не доходил (или AI не ответил)."""
    scores = []
    for r in lot_rows:
        try:
            if r.get("relevance_score") is not None:
                scores.append(int(r["relevance_score"]))
        except (TypeError, ValueError):
            pass
    return max(scores) if scores else None


def missed_is_delivery_loss(lot_rows):
    # type: (Sequence[Dict[str, Any]]) -> bool
    """AI тогда сказал «наш», а алерта нет: лот потерялся между гейтом и
    Telegram (верификатор, дедуп, сбой отправки) — не ошибка классификации."""
    return any(v == "client" for v in _verdicts(lot_rows))


def stage_of(verdict):
    # type: (Any) -> Optional[str]
    """ReplayVerdict → стадия отсева; None — гейт сегодня пропускает."""
    if verdict is None:
        return None
    if not getattr(verdict, "passed_prefilter", False):
        return getattr(verdict, "dropped_at_stage", None) or "prefilter"
    if getattr(verdict, "delivered", None) is False:
        return "ai"
    return None


def is_profile_verdict(verdict):
    # type: (Any) -> bool
    """Прошли префильтр и AI. Сбой AI — не «наш»: прод на нём fail-open, но
    разбор не объявляет победу нашей из-за сетевой ошибки."""
    if verdict is None or not getattr(verdict, "passed_prefilter", False):
        return False
    if getattr(verdict, "ai_error", False):
        return False
    return getattr(verdict, "delivered", None) is True


def printer_like(win, printer_inns, keyword_hit):
    # type: (Dict[str, Any], Iterable[str], Any) -> bool
    """Победитель похож на типографию: ИНН известен или печатное слово в имени.

    keyword_hit(name) -> bool — ТОТ ЖЕ сопоставитель, что у гейта
    (notifier._find_matching_keyword: стем в начале слова), а не своя копия:
    иначе «arakal» (Оракал) засчитался бы внутри «KARAKALPAK».
    """
    inn = win.get("winner_inn")
    if inn and inn in set(printer_inns):
        return True
    name = winner_name(win.get("winner"))
    return bool(name) and bool(keyword_hit(name))


# ── окно и курсор ────────────────────────────────────────────────────────────

def parse_ts(value):
    # type: (Any) -> Optional[datetime]
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def window(cursor_value, now):
    # type: (Any, datetime) -> Tuple[datetime, datetime, bool]
    """[start, end) окна и флаг «прижато». end = now − SAFETY_LAG.

    Курсор = конец прошлого ДОСТАВЛЕННОГО окна. Нет курсора — 7 дней. Курсор
    старше 21 дня (разборы не доходили) — прижимаем и говорим об этом.
    """
    end = now - SAFETY_LAG
    cursor = parse_ts(cursor_value)
    if cursor is None or cursor >= end:
        return end - timedelta(days=FIRST_WINDOW_DAYS), end, False
    floor = end - timedelta(days=MAX_WINDOW_DAYS)
    if cursor < floor:
        return floor, end, True
    return cursor, end, False


def repeat_blocked(state, now):
    # type: (Optional[Dict[str, Any]], datetime) -> Optional[str]
    """Причина отказать повторной отправке (крон дважды, ручной запуск в ту
    же неделю) — или None."""
    if not isinstance(state, dict):
        return None
    delivered = parse_ts(state.get("delivered_at"))
    if delivered is not None and now - delivered < MIN_REPEAT:
        return "разбор уже доставлен %s" % delivered.strftime("%d.%m %H:%M")
    return None


def keyset_filter(last_created_at, last_id):
    # type: (str, str) -> str
    """PostgREST or-фильтр «строго после (created_at, id)».

    Одной колонкой нельзя: upsert пачкой ставит одинаковый now() — замер 01.10:
    290 сделок за неделю на 38 разных created_at. Значения в кавычках — в
    метке времени есть '+' и ':'.
    """
    return 'created_at.gt."%s",and(created_at.eq."%s",id.gt.%s)' % (
        last_created_at, last_created_at, last_id)


def next_state(end, items, undecided_ids, now):
    # type: (datetime, int, Sequence[Any], datetime) -> Dict[str, Any]
    """Состояние после ДОСТАВКИ. Нерешённые (потолок/сбой AI) едут в retry:
    курсор шагает дальше, а они будут судиться снова, а не пропадут."""
    retry = [str(x) for x in undecided_ids if x][:RETRY_CAP]
    return {"cursor": end.isoformat(), "retry": retry, "items": items,
            "delivered_at": now.isoformat()}


def feed_health(current, previous):
    # type: (int, Sequence[int]) -> Tuple[Optional[float], bool]
    """(медиана прошлых окон, «фид просел»). Просел = меньше половины медианы:
    «побед нет» и «фид сделок сломан» иначе выглядят одинаково."""
    prev = [p for p in previous if p is not None]
    if not prev:
        return None, False
    med = _median(prev)
    return med, bool(med) and current < med / 2


# ── сообщение ────────────────────────────────────────────────────────────────

def _mln(value):
    # type: (Optional[float]) -> str
    if value is None:
        return "?"
    try:
        v = float(value) / 1e6
    except (TypeError, ValueError):
        return "?"
    if v >= 100:
        return "{:,.0f}".format(v).replace(",", " ")
    return "{:.1f}".format(v).rstrip("0").rstrip(".")


def _short(text, width):
    # type: (Optional[str], int) -> str
    s = " ".join(str(text or "").split())
    return s if len(s) <= width else s[:width - 1].rstrip() + "…"


def _ddmm(value):
    # type: (Any) -> str
    ts = parse_ts(value)
    if ts is None:
        s = str(value or "")
        return "%s.%s" % (s[8:10], s[5:7]) if len(s) >= 10 else "?"
    return ts.strftime("%d.%m")


def _median(values):
    # type: (Sequence[float]) -> float
    s = sorted(float(v) for v in values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _money_part(win):
    # type: (Dict[str, Any]) -> str
    won, start = win.get("won_price"), win.get("start_price")
    if not is_uzs(win.get("currency")):
        return "%s млн %s" % (_mln(won), html.escape(win.get("currency") or "?")) if won else "цена ?"
    disc = discount_pct(start, won) if is_uzs(win.get("start_currency") or win.get("currency")) else None
    if start and disc is not None:
        return "%s→%s млн (−%s%%)" % (_mln(start), _mln(won), ("%g" % disc))
    return "%s млн" % _mln(won)


def _ai_then(item):
    # type: (Dict[str, Any]) -> str
    score = item.get("ai_then")
    return "AI тогда: %d" % score if score is not None else "до AI тогда не дошёл"


def _where_part(item):
    # type: (Dict[str, Any]) -> str
    status = item.get("lot_status") or item["status"]
    if status == STATUS_ALERTED:
        first = item.get("first_alert") or {}
        how = "пуш" if first.get("telegram_message_id") else "дайджест"
        s = "алерт #%s (%s) · лот с %s" % (first.get("alert_seq"), how, _ddmm(first.get("created_at")))
        s += " · подавали" if item.get("our_action") == "bid" else " · заявка не отмечена"
        if item.get("alert_after_deadline"):
            s += " · ⚠️ лот попал к нам после срока"
        if item["status"] == PRINTER:
            s += " · %s" % _ai_then(item)
        return s
    if status == STATUS_MISSED:
        if item.get("reason") == REASON_DELIVERY_LOSS:
            return "%s — наш, но алерта не было: потеря между гейтом и Telegram" % _ai_then(item)
        if item.get("reason") == REASON_PASSES_TODAY:
            return "%s · сегодня гейт пропускает" % _ai_then(item)
        stage = item.get("stage")
        return "гейт сегодня: %s · %s" % (STAGE_RU.get(stage, stage or "?"), _ai_then(item))
    if item["status"] == PRINTER:
        stage = item.get("stage")
        return "лот не собирали · сделку гейт отсёк: %s" % STAGE_RU.get(stage, stage or "?")
    return "лот не собирали"


def _item_lines(n, item, registry_inns):
    # type: (int, Dict[str, Any], Iterable[str]) -> str
    win = item["win"]
    star = " ★" if win.get("winner_inn") and win["winner_inn"] in set(registry_inns) else ""
    parts = [_money_part(win)]
    if win.get("participants"):
        parts.append("%d уч." % win["participants"])
    parts.append(html.escape(_short(winner_name(win.get("winner")), 28)) + star)
    title = html.escape(_short(win.get("title"), 70))
    url = win.get("source_url") or ""
    head = '%d. <a href="%s">%s</a>' % (n, html.escape(url, quote=True), title) if url else "%d. %s" % (n, title)
    return "%s\n    %s · %s\n    %s" % (
        head, html.escape(_short(win.get("customer"), 40)), " · ".join(parts),
        html.escape(_where_part(item)))


# Порядок = приоритет места в сообщении: сначала то, на чём учится гейт,
# потом «алертили». Иначе «алертили» съедает лимит Telegram, и пропуски не
# влезают (так и было в первом прогоне за 30 дней: 24 строки, пропусков не видно).
SECTIONS = (
    (STATUS_MISSED, "🕳 Наш профиль, но алерта не было", None),
    (PRINTER, "🔎 Выиграли типографии — гейт не узнал", 6),
    (STATUS_NOT_COLLECTED, "📭 Лот не собирали", None),
    (STATUS_ALERTED, "👀 Алертили — забрали другие", 10),
)


def _sum_uzs(items):
    # type: (Iterable[Dict[str, Any]]) -> float
    total = 0.0
    for it in items:
        win = it["win"]
        if is_uzs(win.get("currency")) and win.get("won_price"):
            try:
                total += float(win["won_price"])
            except (TypeError, ValueError):
                pass
    return total


def _by_price(items):
    # type: (Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]
    return sorted(items, key=lambda it: -float(it["win"].get("won_price") or 0))


def build_message(report, registry_inns=()):
    # type: (Dict[str, Any], Iterable[str]) -> str
    """Текст (HTML) недельного разбора. Чистая функция.

    report: {start, end, clamped, items, printers, ours, coverage}. Собирается
    ВСЕГДА — и при нуле побед: молчание неотличимо от поломки.
    """
    registry_inns = tuple(registry_inns or ())
    items = report.get("items") or []
    printers = report.get("printers") or []
    cov = report.get("coverage") or {}
    lines = ["<b>Победы конкурентов · %s–%s</b>" % (_ddmm(report["start"]), _ddmm(report["end"]))]
    if report.get("clamped"):
        lines.append("⚠️ прошлые разборы не доставлены — окно прижато к %d дням" % MAX_WINDOW_DAYS)
    if cov.get("feed_dropped"):
        lines.append("⚠️ сделок в фиде %d при обычных ~%d — фид сделок, похоже, сломан; "
                     "«побед мало» ниже может быть ложью" % (cov.get("deals", 0), cov.get("deals_median") or 0))
    if items:
        head = "Лотов нашего профиля: <b>%d</b> на %s млн сум" % (len(items), _mln(_sum_uzs(items)))
        foreign = sum(1 for it in items if not is_uzs(it["win"].get("currency")))
        if foreign:
            head += " (+%d в другой валюте, в сумму не вошли)" % foreign
        lines.append(head)
    else:
        lines.append("Побед чужих по нашему профилю за окно нет.")
    for win in report.get("ours") or []:
        lines.append("🏆 Мы выиграли: %s · %s" % (html.escape(_short(win.get("title"), 60)), _money_part(win)))

    groups = {PRINTER: list(printers)}
    for status in (STATUS_MISSED, STATUS_NOT_COLLECTED, STATUS_ALERTED):
        groups[status] = [it for it in items if it["status"] == status]
    # Типографии: сначала лоты, которых AI не видел вовсе, — там дыры словаря;
    # отказ AI по увиденному лоту (автозапчасти у «…SHTAMP PROM», балл 0) — после.
    groups[PRINTER] = sorted(groups[PRINTER], key=lambda it: (
        it.get("ai_then") is not None, -float(it["win"].get("won_price") or 0)))
    sections = [(title, groups[st] if st == PRINTER else _by_price(groups[st]), cap)
                for st, title, cap in SECTIONS if groups[st]]

    tail = _tail_lines(report, items, registry_inns)
    budget = _TEXT_BUDGET - len("\n".join(lines)) - len("\n".join(tail)) - 2
    return "\n".join(lines + _render(sections, budget, registry_inns) + tail)


def _render(sections, budget, registry_inns):
    # type: (List[Tuple[str, List[Dict[str, Any]], Optional[int]]], int, Iterable[str]) -> List[str]
    """Секции в бюджет символов. Не влезло — «…и ещё N на X млн», а не молча
    короче: усечённая выборка без пометки выглядит полной."""
    out, used, n = [], 0, 0
    for i, (title, group, cap) in enumerate(sections):
        head = "\n<b>%s — %d · %s млн</b>" % (title, len(group), _mln(_sum_uzs(group)))
        if used + len(head) + 60 > budget:
            left = ", ".join("%s — %d" % (t, len(g)) for t, g, _ in sections[i:])
            out.append("\n…не влезло в сообщение: %s" % left)
            break
        out.append(head)
        used += len(head) + 1
        shown = 0
        for it in group:
            if cap is not None and shown >= cap:
                break
            text = _item_lines(n + 1, it, registry_inns)
            if used + len(text) + 60 > budget:
                break
            n += 1
            shown += 1
            out.append(text)
            used += len(text) + 1
        rest = group[shown:]
        if rest:
            line = "    …и ещё %d на %s млн" % (len(rest), _mln(_sum_uzs(rest)))
            out.append(line)
            used += len(line) + 1
    return out


def _tail_lines(report, items, registry_inns):
    # type: (Dict[str, Any], List[Dict[str, Any]], Iterable[str]) -> List[str]
    tail = []
    registry = set(registry_inns)
    counts = {}  # type: Dict[str, List[Any]]
    for it in items:
        win = it["win"]
        key = win.get("winner_inn") or winner_name(win.get("winner"))
        if key not in counts:
            counts[key] = [winner_name(win.get("winner")), 0, win.get("winner_inn") in registry]
        counts[key][1] += 1
    if counts:
        top = sorted(counts.values(), key=lambda v: (-v[1], v[0]))[:6]
        tail.append("\nКто забирал: " + ", ".join(
            "%s%s ×%d" % (html.escape(_short(name, 24)), " ★" if star else "", cnt) for name, cnt, star in top))
    parts = [it["win"].get("participants") for it in items if it["win"].get("participants")]
    discs = [discount_pct(it["win"].get("start_price"), it["win"].get("won_price")) for it in items
             if is_uzs(it["win"].get("currency"))]
    discs = [d for d in discs if d is not None]
    if parts and discs:
        tail.append("Конкуренция: медиана %s уч., скидка медиана %s%%" % (
            ("%g" % _median(parts)), ("%g" % round(_median(discs), 1))))
    cov = report.get("coverage") or {}
    line = "Покрытие: сделок %d" % cov.get("deals", 0)
    if cov.get("deals_median"):
        line += " (обычно ~%d)" % cov["deals_median"]
    line += " · победитель скрыт %d" % cov.get("hidden_winner", 0)
    if cov.get("unlinked"):
        line += " · без ссылки на лот %d" % cov["unlinked"]
    line += " · вне профиля %d" % cov.get("not_profile", 0)
    if cov.get("human_rejected"):
        line += " · вы сами пометили «мимо» %d" % cov["human_rejected"]
    if cov.get("ai_cap"):
        line += " · AI %d/%d" % (cov.get("ai_used", 0), cov["ai_cap"])
    if cov.get("ai_errors"):
        line += " · сбоев AI %d" % cov["ai_errors"]
    if cov.get("undecided"):
        line += " · не решено %d — перенесено на следующий разбор" % cov["undecided"]
    tail.append(line)
    tail.append("★ — из реестра конкурентов. Только etender: у ВМК-69 нет ИНН, "
                "XT-Xarid и Hayotbirja победителя не публикуют.")
    if any(it.get("stage") == "no_keyword" for it in (report.get("printers") or [])):
        tail.append("«нет ключевого слова» — кандидат в словарь; проверяется через shadow "
                    "(shadow_search --add-keyword), в бой — по итогам недели.")
    return tail


def plain_text(html_text):
    # type: (str) -> str
    """Запасной вариант для Telegram: без разметки. Одна кривая сущность в HTML
    иначе блокировала бы доставку каждую неделю — и разбор снова молчал бы."""
    return html.unescape(_TAG_RE.sub("", html_text))
