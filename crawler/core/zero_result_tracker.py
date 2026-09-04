"""Zero-result tracker — task #6 / RISK-1 mitigation.

Alerts the operator when a source silently returns 0 tenders for 3 consecutive
crawl cycles. Recovery message is sent once when data returns.

Key design choices (see `.claude/teams/feature-eimzo-reliability/DECISIONS.md`):

1. ``skipped_no_auth`` does NOT count as a zero. Token is absent, not the API.
2. Newly-observed sources are exempt for the first 3 cycles (grace period).
3. Alert storm prevention: at most one alert per source until it recovers.
4. State lives in ``crawler_settings`` under ``ZERO_RESULT_STATE_KEY``, accessed
   via ``session_store.get_setting / set_setting`` (see
   ``.conventions/anti-patterns/no-direct-supabase.md``).
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from crawler.auth.constants import ZERO_RESULT_STATE_KEY
from crawler.auth.session_store import session_store
from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────
GRACE_CYCLES = 3        # new sources exempt for first N cycles
ALERT_THRESHOLD = 3     # consecutive zeros before we alert
STATE_VERSION = 1

# ── Outcome classification (string keys to stay JSON-serializable) ──────────
OK_WITH_DATA = "ok_with_data"
OK_EMPTY = "ok_empty"
SKIPPED_NO_AUTH = "skipped_no_auth"
ERROR = "error"


def classify_outcome(count, skipped_no_auth, error):
    # type: (int, bool, Optional[str]) -> str
    """Translate runner-provided signals to a single outcome string.

    Precedence: skipped_no_auth > error > empty > with_data.
    Rationale: if auth was missing we never reached the API — treat that
    first, regardless of whether count happened to be 0.
    """
    if skipped_no_auth:
        return SKIPPED_NO_AUTH
    if error:
        return ERROR
    if count > 0:
        return OK_WITH_DATA
    return OK_EMPTY


def _now_iso():
    # type: () -> str
    return datetime.now(timezone.utc).isoformat()


def _empty_source_state():
    # type: () -> Dict
    return {
        "cycles_observed": 0,
        "consecutive_zeros": 0,
        "last_outcome": None,
        "last_observed_at": None,
        "alerted": False,
        "alerted_at": None,
    }


def _load_state():
    # type: () -> Dict
    """Load state dict. Returns a valid v1 shell on miss/corrupt."""
    raw = session_store.get_setting(ZERO_RESULT_STATE_KEY)
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "sources": {}}
    # Defensive: ensure "sources" is a dict
    if not isinstance(raw.get("sources"), dict):
        raw["sources"] = {}
    return raw


def _save_state(state):
    # type: (Dict) -> bool
    return session_store.set_setting(ZERO_RESULT_STATE_KEY, state)


async def _send_telegram(text):
    # type: (str) -> bool
    """Send a silent TG message to the alert chat. Returns True on success."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.info("[ZeroResult] TG creds missing — skipping alert")
        return False
    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "disable_notification": True,
                "protect_content": True,
            })
            return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.warning("[ZeroResult] TG send failed: %s", str(exc)[:80])
        return False


def advance_source_state(prior, outcome):
    # type: (Dict, str) -> Dict
    """Pure-function state transition. Returns the NEW source state.

    Does NOT send alerts — caller decides based on the returned diff.
    Split out so unit tests (task #7) can exercise transitions without I/O.
    """
    st = dict(prior) if prior else _empty_source_state()
    st["last_outcome"] = outcome
    st["last_observed_at"] = _now_iso()

    if outcome == SKIPPED_NO_AUTH:
        # Neither counts as zero nor increments cycles_observed — the source
        # had no chance to speak. Missing token is a separate healthcheck signal.
        return st

    # Any non-skipped outcome counts as a cycle observed.
    st["cycles_observed"] = int(st.get("cycles_observed", 0)) + 1

    if outcome == OK_WITH_DATA:
        st["consecutive_zeros"] = 0
        # Recovery is the CALLER's decision based on prior["alerted"].
        # We just clear the flag here so the next zero streak starts fresh.
        st["alerted"] = False
        st["alerted_at"] = None
    else:
        # OK_EMPTY or ERROR → counts as a zero.
        st["consecutive_zeros"] = int(st.get("consecutive_zeros", 0)) + 1

    return st


def should_alert(prior, new_state):
    # type: (Dict, Dict) -> bool
    """Decide whether to emit a fresh alert for this source.

    Rules:
    - Never alert during grace (cycles_observed < GRACE_CYCLES).
    - Alert exactly once per zero streak (prior.alerted == False).
    - Require ALERT_THRESHOLD consecutive zeros.
    """
    if new_state.get("cycles_observed", 0) < GRACE_CYCLES:
        return False
    if prior and prior.get("alerted"):
        return False
    if new_state.get("consecutive_zeros", 0) < ALERT_THRESHOLD:
        return False
    return new_state.get("last_outcome") in (OK_EMPTY, ERROR)


def should_send_recovery(prior, new_state):
    # type: (Dict, Dict) -> bool
    """Recovery fires once when a previously-alerted source returns data."""
    if not prior or not prior.get("alerted"):
        return False
    return new_state.get("last_outcome") == OK_WITH_DATA


TG_TEXT_LIMIT = 4096  # жёсткий предел Telegram на текст сообщения
_STANDING_LIMIT = 12  # строк «молчат давно» в сводке; хвост схлопывается в «и ещё N»


def standing_silences(sources_state):
    # type: (Dict[str, Dict]) -> List[str]
    """Строки «молчат давно»: тревога уже уходила, а данных так и нет.

    Из чего выросло (01.09). Сводка показывала только ПЕРЕХОДЫ (pending_*):
    источник, замолчавший однажды, получает alerted=True и исчезает из всех
    последующих сводок. Так etender с 1-based пагинацией молчал 12 дней
    (19-31.08) и не всплыл ни 24.08, ни 31.08 — пересечение порога случилось
    до появления pending-механики, нового перехода не было. Нашли руками.
    Секция строится из СОСТОЯНИЯ: молчишь — виден каждый понедельник,
    пока не оживёшь или источник не выключат.

    Свежие пересечения этой недели (pending_alert) не дублируются — они уже
    в секции «Молчат».
    """
    rows = []
    for sid in sorted(sources_state):
        st = sources_state[sid]
        if not isinstance(st, dict) or not st.get("alerted") or st.get("pending_alert"):
            continue
        if st.get("last_outcome") not in (OK_EMPTY, ERROR):
            continue
        zeros = int(st.get("consecutive_zeros", 0))
        since = ""
        raw = st.get("alerted_at")
        if raw:
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                since = ", с %s" % dt.strftime("%d.%m")
            except (ValueError, TypeError):
                since = ""
        rows.append((zeros, "%s — %d циклов%s" % (sid, zeros, since)))
    rows.sort(key=lambda r: -r[0])
    return [r[1] for r in rows]


def _render_digest(alerts, recoveries, standing, caps):
    # type: (List[str], List[str], List[str], List[int]) -> str
    """Сборка текста при заданных капах на секцию. Счётчик в заголовке — ПОЛНЫЙ,
    показанных строк может быть меньше: «Молчат давно (21):» + 12 строк + хвост."""
    lines = ["\U0001f4e1 Источники за неделю", ""]
    sections = (("Молчат", alerts, caps[0]),
                ("Снова с данными", recoveries, caps[1]),
                ("Молчат давно", standing, caps[2]))
    for title, items, cap in sections:
        if not items:
            continue
        if len(lines) > 2:
            lines.append("")
        lines.append("%s (%d):" % (title, len(items)))
        lines.extend("\u00b7 " + x for x in items[:cap])
        hidden = len(items) - cap
        if hidden > 0:
            lines.append("\u00b7 … и ещё %d" % hidden)
    lines.append("")
    lines.append("Поломка сбора приходит не отсюда, а из healthcheck (06:00).")
    return "\n".join(lines)


def _weekly_digest(alerts, recoveries, standing=None, limit=TG_TEXT_LIMIT):
    # type: (List[str], List[str], Optional[List[str]], int) -> str
    """Одно сообщение вместо пачки. Чистая функция — тестируется без сети.

    БЕЗ Markdown: `_send_telegram` этого модуля шлёт без parse_mode, и звёздочки
    ушли бы в чат буквально. Первая редакция (22.08 утром) была с разметкой —
    поймано независимой проверкой до первого понедельника.

    Кап 4096 (05.09). Telegram отдаёт 400 на длинный текст, а неудачная отправка
    ОСТАВЛЯЕТ pending-флаги — сводка, однажды переросшая лимит, не ушла бы уже
    никогда. В состоянии 101 источник; неделя массового восстановления (прокси
    ожил + tg-каналы проснулись) давала ~7,5k символов. Режем по секциям, от
    наименее срочной: «молчат давно» → «снова с данными» → «молчат»; счётчики в
    заголовках остаются полными, чтобы усечение было видно, а не выглядело
    «источников стало меньше».
    """
    alerts = list(alerts or [])
    recoveries = list(recoveries or [])
    standing = list(standing or [])
    targets = [len(alerts), len(recoveries), min(len(standing), _STANDING_LIMIT)]
    caps = [0, 0, 0]
    # Растим секции ПО КРУГУ, а не режем сверху. Первая редакция (05.09) резала
    # от наименее срочной секции вниз — и на живом состоянии прода схлопывала
    # «снова с данными» и «молчат давно» в ноль, показывая 77 тревог и оставляя
    # 892 символа бюджета неиспользованными. Круговой рост делит место честно:
    # каждая секция получает по строке за круг, пока следующая строка влезает.
    # Порядок в круге — по срочности: тревоги, восстановления, давние молчуны.
    while True:
        grew = False
        for idx in (0, 1, 2):
            if caps[idx] >= targets[idx]:
                continue
            trial = list(caps)
            trial[idx] += 1
            if len(_render_digest(alerts, recoveries, standing, trial)) <= limit:
                caps = trial
                grew = True
        if not grew:
            break
    text = _render_digest(alerts, recoveries, standing, caps)
    if len(text) > limit:
        # Пояс: при нынешних заголовке и футере недостижим — секции опускаются
        # до нуля раньше, и даже строка в 9000 символов схлопывается в «и ещё 1»
        # (пин test_pathological_single_line_degrades_to_a_counter). Оставлен на
        # случай, если шапка когда-нибудь распухнет: лучше обрез, чем 400 от TG.
        text = text[:limit - 1] + "…"
    return text


def _digest_sent_this_week(state, now=None):
    # type: (Dict, Optional[datetime]) -> bool
    """Уже ли уходила сводка на этой ISO-неделе. Сломанная метка = «не уходила»:
    лучше редкий дубль, чем молча потерянная неделя."""
    raw = state.get("last_weekly_digest_at")
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    now = now or datetime.now(timezone.utc)
    return last.isocalendar()[:2] == now.isocalendar()[:2]


def recovery_is_due(now=None):
    # type: (Optional[datetime]) -> bool
    """Шлём ли сегодня «снова с данными».

    Правило — раз в неделю, по понедельникам UTC (просьба Данияра 30.06:
    восстановление источника не критично, а каждый цикл оно шумит). Поломка
    сбора при этом уходит сразу, в любой день.

    Вынесено из тела `track_and_alert` отдельной функцией потому, что было
    инлайн-условием на `datetime.now()`, и два теста на восстановление
    проходили ТОЛЬКО по понедельникам, а остальные шесть дней недели считались
    «предсуществующими падениями сьюта». Тест, зависящий от дня запуска, не
    тест — теперь правило подменяется в тестах и закреплено отдельно.
    """
    now = now or datetime.now(timezone.utc)
    return now.weekday() == 0


async def track_and_alert(outcomes, dry_run=False):
    # type: (Dict[str, Dict], bool) -> int
    """Update persisted state and send alerts/recoveries.

    Args:
        outcomes: per-source signals from runner, e.g.
            {"ebirja-rs": {"count": 0, "skipped_no_auth": False, "error": None}}
        dry_run: if True, state is NOT persisted and no TG messages are sent.

    Returns: number of TG messages successfully sent (alerts + recoveries).
    """
    if not outcomes:
        return 0

    state = _load_state()
    sources_state = state["sources"]  # type: Dict[str, Dict]

    alerts_to_send = []    # type: List[str]
    recoveries_to_send = []  # type: List[str]

    for sid, signals in outcomes.items():
        outcome = classify_outcome(
            int(signals.get("count", 0)),
            bool(signals.get("skipped_no_auth", False)),
            signals.get("error"),
        )
        prior = sources_state.get(sid)
        new_state = advance_source_state(prior, outcome)

        # Переходы ПОМЕЧАЮТСЯ в состоянии, а не отправляются с места (22.08,
        # вторая редакция). Первая редакция недельного гейта собирала сообщение
        # на прогоне пересечения порога и отправляла только если «сегодня
        # понедельник». Но should_alert срабатывает РОВНО ОДИН РАЗ и ставит
        # alerted=True — на следующих прогонах он уже молчит. Источник, замолчавший
        # во вторник, не попадал ни в один понедельник: тревога терялась
        # навсегда. Тесты этого не видели, потому что фикстура делала каждый день
        # понедельником. Найдено независимой проверкой, не тестами.
        #
        # То же самое с «снова с данными» было с 30.06 — недельный гейт для
        # recovery стоял на той же ошибке.
        if should_alert(prior, new_state):
            reason = "ошибка: %s" % signals["error"][:100] if outcome == ERROR else "0 тендеров подряд"
            msg = "%s молчит %d циклов (%s)" % (sid, new_state["consecutive_zeros"], reason)
            alerts_to_send.append(msg)
            new_state["alerted"] = True
            new_state["alerted_at"] = _now_iso()
            new_state["pending_alert"] = msg
        elif should_send_recovery(prior, new_state):
            msg = "%s снова с данными (count=%d)" % (sid, int(signals.get("count", 0)))
            if prior and prior.get("pending_alert"):
                # тревога так и не была доставлена — скажем об этом, а не
                # сделаем вид, что «снова» относится к чему-то известному
                msg += " — молчал, тревога не успела уйти"
            recoveries_to_send.append(msg)
            new_state["pending_recovery"] = msg
            new_state.pop("pending_alert", None)
            # new_state.alerted was already reset to False inside advance_source_state

        sources_state[sid] = new_state

    sent = 0
    if not dry_run:
        # 22.08 — решение Данияра «убрать миллиард алертов, оставить недельную
        # сводку». Раньше «молчит N циклов» уходило В КАЖДОМ цикле и отдельным
        # сообщением на источник: 1-5 штук каждые два часа. Порог GRACE_CYCLES=3
        # это ~6 часов тишины — осмысленно для площадки с ежечасными лотами и
        # бессмысленно для Telegram-канала, который пишет 0-1 раз в НЕДЕЛЮ.
        # Отсюда весь шум: tg-uzex, tg-mitc, tg-hamkorbank «молчали» по расписанию.
        #
        # Теперь: состояние считается каждый цикл (машина состояний не тронута),
        # а доставка — раз в неделю и ОДНИМ сообщением. Настоящая поломка сбора
        # ловится не отсюда, а healthcheck'ом (06:00, --alert-on-fail) и
        # freshness_watchdog (07:00) — они остались ежедневными.
        # Сводка строится из СОСТОЯНИЯ (всё недоставленное), а не из переходов
        # этого прогона — см. комментарий в цикле выше. После успешной отправки
        # флаги снимаются; при отказе Telegram остаются и уйдут следующим прогоном.
        pend_alerts = [sources_state[k]["pending_alert"] for k in sorted(sources_state)
                       if sources_state[k].get("pending_alert")]
        pend_recov = [sources_state[k]["pending_recovery"] for k in sorted(sources_state)
                      if sources_state[k].get("pending_recovery")]
        # ОДНА сводка в неделю, а не одна на понедельничный краул. Первый живой
        # понедельник (24.08) показал дыру второй редакции: флаги снимаются
        # после отправки, но КАЖДЫЙ следующий краул этого же понедельника с
        # новыми переходами слал свою мини-сводку — 3 сообщения за ночь
        # (00:02, 00:30, 02:30). Маркер last_weekly_digest_at держит неделю:
        # переход, случившийся в понедельник днём, ждёт СЛЕДУЮЩЕГО понедельника
        # — ровно как переход вторника, это уже принятый компромисс.
        # «Молчат давно» — из состояния, без флагов: секция сама возвращается
        # каждый понедельник, пока источник молчит (урок etender 19-31.08).
        # Из-за неё сводка уходит и в неделю БЕЗ новых переходов.
        standing = standing_silences(sources_state)
        if (recovery_is_due() and (pend_alerts or pend_recov or standing)
                and not _digest_sent_this_week(state)):
            if await _send_telegram(_weekly_digest(pend_alerts, pend_recov, standing)):
                sent = 1
                state["last_weekly_digest_at"] = _now_iso()
                for st in sources_state.values():
                    st.pop("pending_alert", None)
                    st.pop("pending_recovery", None)
        _save_state(state)
    else:
        logger.info(
            "[ZeroResult] dry_run — would send %d alerts + %d recoveries",
            len(alerts_to_send), len(recoveries_to_send),
        )

    if alerts_to_send or recoveries_to_send:
        logger.info(
            "[ZeroResult] %d alerts, %d recoveries (%d sent, dry_run=%s)",
            len(alerts_to_send), len(recoveries_to_send), sent, dry_run,
        )
    return sent
