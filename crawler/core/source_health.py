"""Единый реестр здоровья источника — то, что должны знать ВСЕ сторожа.

Из чего выросло (05.09). Про «этот источник молчит по известной причине» знали
три разных места и по-разному: `healthcheck.DEAD_SOURCES_WHITELIST` (по именам,
локальная переменная внутри метода), `freshness_watchdog.KNOWN_RETIRED` (тоже
по именам, свой список) и `sources.yaml` через `enabled: false`. Zero-result
трекер не знал ничего и в первой же недельной сводке потребовал действия по
семи источникам, про которые решение принято месяцы назад.

Здесь список живёт один раз; сторожа импортируют его отсюда. Ключи — ИМЕНА
источников (так исторически сложилось в healthcheck и так их видит БД), для
трекера есть перевод в id через `excused_source_ids`.
"""

import logging
from typing import Optional, Set

import yaml

logger = logging.getLogger(__name__)


# Источники, чьё молчание объяснено и тревоги не требует.
#
# ЗАЧЕМ ЗДЕСЬ ПРИЧИНЫ. До 11.08 это было просто множество имён с групповыми
# комментариями и припиской «пересматривать ежеквартально» — пересматривать
# было НЕ ПО ЧЕМУ: ни даты, ни замера, ни того, кто и почему внёс. Список
# такого вида растёт и молча глушит сторожа. Формат теперь тот же, что у
# funnel_watchdog.KNOWN_SILENT: причина с датой и проверяемым фактом.
#
# Проверка принадлежности работает по ключам, поэтому `name not in ...`
# ниже читается как раньше.
_LEGACY = "унаследовано до 11.08 без записанной причины — перепроверить"
DEAD_SOURCES_WHITELIST = {
    # Международные организации — узбекских тендеров у них мало
    'UNDP Procurement': _LEGACY,
    'UN Global Marketplace': _LEGACY,
    'World Bank': _LEGACY,
    'Asian Development Bank': _LEGACY,
    'Islamic Development Bank (IsDB)': _LEGACY,
    'EBRD': _LEGACY,
    'GIZ': _LEGACY,
    'JICA': _LEGACY,
    'KOICA': _LEGACY,
    'USAID': _LEGACY,
    'EU TED': _LEGACY,
    # Банки — публикуют тендеры раз в квартал
    'InFinBank': _LEGACY,
    'Orient Finance Bank': _LEGACY,
    'Sanoat Qurilish Bank': _LEGACY,
    'Asia Alliance Bank': _LEGACY,
    'Hamkorbank': _LEGACY,
    # Малообъёмные TG-зеркала
    'TG: PR UZB (запросы клиентов)': _LEGACY,
    'TG: UZEX Xarid Official': _LEGACY,
    'TG: Закупки Prom.uz': _LEGACY,
    'TG: Фонд предпринимательства': _LEGACY,
    'TG: Узбекистон Темир Йуллари': _LEGACY,
    'TG: Хамкорбанк': _LEGACY,
    'TG: Мин ИТ': _LEGACY,
    # Cooperation legacy (заменены на cooperation-plans-filtered)
    'Cooperation.uz Брошюры/Буклеты': _LEGACY,
    'Cooperation.uz Аукционы': _LEGACY,
    'Cooperation.uz Закупочные планы': _LEGACY,
    'Cooperation.uz Э-магазин лоты': _LEGACY,
    'Cooperation.uz Bosma (узб.)': _LEGACY,
    # Прочие тихие зеркала
    'Узбекистон Темир Йуллари': _LEGACY,
    'Минстрой (tender.mc.uz)': _LEGACY,
    'E-Birja активные аукционы (xarid)': _LEGACY,

    # --- Разбор 11.08: семь источников, на которые сторож звенел ежедневно.
    # Общая причина не чинить: пять из шести не дали НИ ОДНОГО алерта за всю
    # историю наблюдений, у TenderWeek — 9 алертов на 476 строк.
    'OSCE Uzbekistan':
        'у узбекистанского офиса ОБСЕ нет открытых тендеров: прогон источника '
        'проходит без ошибок и отдаёт 0, нефильтрованный /tenders отдаёт 10 лотов '
        '(Косово, Албания, Ашхабад, Бишкек, Душанбе), Узбекистана ни одного. '
        'Соединение починено 05.08 (use_system_ca), дело не в нас (11.08)',
    'TG: Beeline Tenders':
        'канал пишет 0-1 сообщение в НЕДЕЛЮ: 12 прогонов в день, ошибок нет, '
        'по истории fetched=1 за 03.08 и 28.07. Недельное окно для него слишком '
        'узкое, это не поломка (11.08)',
    'TG: UNDP UZB Tenders':
        'то же, что Beeline: канал молчит неделями, механизм исправен — 13 других '
        'TG-каналов собрались в тот же день (11.08)',
    'Хамкорбанк':
        'на странице банка тендерных позиций в серверном HTML нет, но покрытие НЕ '
        'потеряно: TG-канал того же банка жив и дал 19 строк за 7 дней. '
        'HTML-источник избыточен (11.08)',
    'TenderWeek.com':
        'листинг ушёл за логин: в серверном HTML только навигация и «Войти / Стать '
        'закупщиком». Регистрация — за Данияром; выхлоп исторически мал, '
        '9 алертов на 476 строк (11.08)',
    'Tashkent Steel (ТМЗ)':
        'СЛОМАН У НАС, чинить решено не стоит: сайт перестроен (был Elementor, стал '
        '«XARID TMZ»), селектор h2.elementor-heading-title мёртв, на странице 4 '
        'активных лота. Но за всю историю источник дал 1 строку и 0 алертов — '
        'сталелитейный завод закупает подшипники и прокат. Если понадобится — '
        'переписать селекторы (11.08)',
    'Ипотека-банк':
        'СЛОМАНА СВЯЗЬ, чинить решено не стоит: с Мака страница отдаёт HTTP 301 за '
        '5,6 с, с VPS — таймаут SSL-рукопожатия, то есть площадка не пускает '
        'датацентровый IP (тот же класс, что cooperation.uz — лечится резидентным '
        'прокси). За всю историю 20 строк и 0 алертов (11.08)',
}


def excused_source_ids(config_path):
    # type: (str) -> Set[str]
    """id источников из sources.yaml, чьё молчание объяснено.

    Whitelist ведётся по именам, zero-result трекер работает по id — перевод
    делается здесь, чтобы ни один сторож не заводил свою копию списка. Сбой
    чтения конфига = пустое множество: лучше лишняя строка в сводке, чем
    проглоченная тревога.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("excused_source_ids: %s", str(exc)[:120])
        return set()
    return {s.get("id") for s in (raw.get("sources") or [])
            if s.get("id") and s.get("name") in DEAD_SOURCES_WHITELIST}


# ── Вес источника и порог протухания ────────────────────────────────────────
#
# Дыра, которую это закрывает (аудит 01.09, инцидент 29.08-04.09). Резидентный
# прокси упёрся в 402, и вместе с ним встали Cooperation.uz Лоты и UZEX
# Предквалификации — источники №2 и №3 по алертам, 43% недельного потока.
# Сигналы шли: proxy_health_check писал «IPRoyal 402» каждые 12 часов,
# healthcheck — «token.cooperation EXPIRED» каждые пять. Около 25 сообщений за
# 3,5 дня, и ни одно не сказало «стоят два источника, это 43% потока».
# Сторожа знали про хосты и токены, но не про вес того, что за ними.
#
# 12 часов для geo-проверки — не порог: `run_proxy_fetch` ходит в 03:30, 09:30
# и 15:30, штатный разрыв между прогонами ровно 12 часов. Тревога начинается
# там, где пропущено два прогона подряд.

HEAVY_SHARE_PCT = 5.0     # источник считается тяжёлым от этой доли алертов
HEAVY_STALE_HOURS = 24    # два пропущенных прогона подряд — это уже поломка


def _sources_we_never_push():
    # type: () -> set
    """Источники, чьи алерты выключены решением, а не поломкой.

    Замер на живой базе 05.09 показал, почему это обязательно: за 30 дней в
    «тяжёлые» попали «UZEX Э-магазин бумага и изделия» (8.3%) и «UZEX Э-магазин
    печатные услуги» (6.5%) — их пуши отключены 22.08 («не нужны алерты из
    е-магазина UZEX вообще»), а окно в 30 дней ещё захватывает доотключённую
    историю. Без этого фильтра остановка сознательно заглушенного источника
    подняла бы FAIL «встал источник, 8% потока» — ложная тревога ровно того
    сорта, ради борьбы с которым проверка и делалась.
    """
    try:
        from crawler.core.notifier import _NO_PUSH_SOURCES

        return set(_NO_PUSH_SOURCES)
    except Exception as exc:  # notifier тянет прод-зависимости — не падаем
        logger.warning("_sources_we_never_push: %s", str(exc)[:120])
        return set()


def source_weights(days=30, min_share=HEAVY_SHARE_PCT):
    # type: (int, float) -> dict
    """Вес источников по доле алертов за `days` дней.

    Отдаёт {"weights": {source: {"alerts": n, "pct": x}}, "total": N,
    "heavy": [source, ...]}. Считает по алертам, а не по собранным строкам:
    сторожу важно, сколько мы ПОТЕРЯЕМ, а не сколько строк перестало капать.

    Источники, чьи пуши выключены решением, не считаются вовсе — ни в долях,
    ни в знаменателе (см. `_sources_we_never_push`).
    """
    from datetime import datetime, timedelta, timezone

    from crawler.core.db import iter_rows

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    muted = _sources_we_never_push()
    counts = {}
    total = 0
    for page in iter_rows("tenders", "source,alert_seq,created_at",
                          filters=[("gte", ("created_at", since)),
                                   ("gte", ("alert_seq", 1))],
                          order_col="created_at", label="source_weights"):
        for row in page:
            src = row.get("source")
            if not src or src in muted:
                continue
            counts[src] = counts.get(src, 0) + 1
            total += 1
    weights = {}
    for src, n in counts.items():
        weights[src] = {"alerts": n, "pct": round(n * 100.0 / total, 1) if total else 0.0}
    heavy = sorted([s for s, w in weights.items() if w["pct"] >= min_share],
                   key=lambda s: -weights[s]["pct"])
    return {"weights": weights, "total": total, "heavy": heavy}


def impact_line(weights, source_names):
    # type: (dict, list) -> str
    """Человеческая приписка к тревоге: что именно стоит и сколько это потока.

    Без неё алерт «token.cooperation EXPIRED» читается как строчка про токен,
    хотя за ним 43% алертов.
    """
    parts = []
    share = 0.0
    for name in source_names:
        w = (weights or {}).get(name)
        if not w:
            continue
        parts.append("%s (%s%%)" % (name, w["pct"]))
        share += w["pct"]
    if not parts:
        return ""
    return "за этим стоят: %s — %.0f%% алертов за 30 дней" % (", ".join(parts), share)


# ── Единый реестр здоровья источника ────────────────────────────────────────
#
# ЗАЧЕМ (аудит 01.09, реализация 05.09). Про здоровье источника знали пять
# сторожей, и каждый по-своему: zero-result-трекер считал циклы молчания,
# freshness_watchdog — дни с последней строки, healthcheck — свежесть geo и
# «мёртвые за 7 дней», funnel_watchdog — падение объёма, proxy_health_check —
# доступность прокси. Списков исключений было три: `enabled` в sources.yaml,
# DEAD_SOURCES_WHITELIST и KNOWN_RETIRED. Простой прокси 29.08-04.09 показал
# цену: сигналы шли из четырёх мест, и ни один не сказал «встали два источника,
# это 43% потока» — сложить картину было негде.
#
# Реестр складывает её в одном месте: конфиг + вес + свежесть + состояние
# трекера + вердикт. Сторожа читают отсюда, человек смотрит одной командой.

VERDICT_OK = "ok"
VERDICT_SILENT = "silent"                  # молчит, объяснений нет
VERDICT_SILENT_EXPECTED = "silent_expected"  # молчит, и это решение
VERDICT_HEAVY_STALE = "heavy_stale"        # тяжёлый и протух — это поломка
VERDICT_NEVER = "never"                    # ни одной строки за всю историю

# Источники, которые собираются НЕ через runner (`fetch_cooperation.py` под
# резидентным прокси, разовые скрипты) в sources.yaml не значатся. Реестр,
# построенный только по конфигу, терял Cooperation.uz Лоты — источник №2 по
# алертам, 26% потока (замер 05.09, поймано первым же прогоном на живых данных).
# Берём такие из базы, если они живы: свежее этого окна или дают алерты.
EXTERNAL_FRESH_DAYS = 30


def _freshness_rows():
    # type: () -> list
    """`source_freshness` — по строке на источник: source, cnt, last_collected."""
    from crawler.core.db import _get_client

    try:
        return (_get_client().rpc("source_freshness").execute().data) or []
    except Exception as exc:
        logger.warning("source_freshness rpc: %s", str(exc)[:120])
        return []


def _config_rows(config_path):
    # type: (str) -> list
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("build_registry: конфиг не прочитан: %s", str(exc)[:120])
        return []
    return [s for s in (raw.get("sources") or []) if s.get("id")]


def build_registry(config_path, days=30, now=None):
    # type: (str, int, Optional[object]) -> dict
    """Полная картина по каждому источнику конфига.

    Отдаёт {"sources": [...], "alerts_total": N, "generated_at": iso}. Каждая
    запись несёт id, имя, флаги решений (выключен / молчание объяснено / пуши
    заглушены), вес в потоке алертов, свежесть данных и состояние трекера.

    Ходит в сеть трижды (веса, свежесть, состояние трекера) и НИЧЕГО не пишет:
    это витрина, а не сторож. Решения принимают вызывающие.
    """
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    weights_rep = source_weights(days=days)
    weights = weights_rep["weights"]
    fresh = {}
    for row in _freshness_rows():
        name = row.get("source") or ""
        if name:
            fresh[name] = row
    muted = _sources_we_never_push()
    excused_names = set(DEAD_SOURCES_WHITELIST)

    try:
        from crawler.auth.constants import ZERO_RESULT_STATE_KEY
        from crawler.auth.session_store import session_store

        tracker = (session_store.get_setting(ZERO_RESULT_STATE_KEY) or {}).get("sources") or {}
    except Exception as exc:
        logger.warning("build_registry: состояние трекера недоступно: %s", str(exc)[:120])
        tracker = {}

    out = []
    for cfg in _config_rows(config_path):
        sid = cfg["id"]
        name = cfg.get("name") or sid
        row = fresh.get(name) or {}
        last_raw = row.get("last_collected")
        silent_h = None
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
                silent_h = (now - last_dt).total_seconds() / 3600.0
            except (ValueError, TypeError):
                silent_h = None
        st = tracker.get(sid) or {}
        rec = {
            "id": sid,
            "name": name,
            "enabled": bool(cfg.get("enabled", True)),
            "external": False,
            "excused": name in excused_names,
            "excuse": silence_excuse(name),
            "muted_push": name in muted,
            "alerts": (weights.get(name) or {}).get("alerts", 0),
            "share_pct": (weights.get(name) or {}).get("pct", 0.0),
            "rows": int(row.get("cnt") or 0),
            "last_collected": str(last_raw)[:19] if last_raw else None,
            "silent_hours": None if silent_h is None else round(silent_h, 1),
            "zeros": int(st.get("consecutive_zeros") or 0),
            "alerted": bool(st.get("alerted")),
            "rhythm_hours": None,
            "threshold_hours": None,
        }
        gaps = st.get("data_gaps") or []
        if gaps:
            from crawler.core.zero_result_tracker import silence_threshold_hours

            ordered = sorted(g for g in gaps if isinstance(g, (int, float)) and g > 0)
            if ordered:
                rec["rhythm_hours"] = ordered[len(ordered) // 2]
            rec["threshold_hours"] = silence_threshold_hours(st)
        rec["verdict"] = _verdict(rec)
        out.append(rec)

    # Источники вне конфига: собираются отдельными скриптами, но это такие же
    # источники, и молчание Cooperation.uz Лоты стоит 26% потока.
    known_names = {r["name"] for r in out}
    for name, row in sorted(fresh.items()):
        if name in known_names or not name:
            continue
        silent_h = None
        last_raw = row.get("last_collected")
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
                silent_h = (now - last_dt).total_seconds() / 3600.0
            except (ValueError, TypeError):
                silent_h = None
        alive = (name in weights) or (silent_h is not None
                                      and silent_h <= EXTERNAL_FRESH_DAYS * 24)
        if not alive:
            continue
        rec = {
            "id": "external:%s" % name,
            "name": name,
            "enabled": True,
            "external": True,
            "excused": name in excused_names,
            "excuse": silence_excuse(name),
            "muted_push": name in muted,
            "alerts": (weights.get(name) or {}).get("alerts", 0),
            "share_pct": (weights.get(name) or {}).get("pct", 0.0),
            "rows": int(row.get("cnt") or 0),
            "last_collected": str(last_raw)[:19] if last_raw else None,
            "silent_hours": None if silent_h is None else round(silent_h, 1),
            "zeros": 0, "alerted": False,
            "rhythm_hours": None, "threshold_hours": None,
        }
        rec["verdict"] = _verdict(rec)
        out.append(rec)

    out.sort(key=lambda r: (-r["share_pct"], r["name"]))
    return {"sources": out, "alerts_total": weights_rep["total"],
            "generated_at": now.isoformat()}


def _verdict(rec):
    # type: (dict) -> str
    """Один вердикт вместо пяти мнений. Решение человека всегда сильнее замера:
    выключенный или объяснённый источник не «сломан», он молчит по договорённости.

    Объяснением считается любая из шести категорий `silence_excuse`, а не только
    whitelist: выведенный из эксплуатации и зеркало дедупа молчат так же законно.
    """
    if not rec["enabled"] or rec["excused"] or rec["muted_push"] or rec.get("excuse"):
        return VERDICT_SILENT_EXPECTED if rec["silent_hours"] is None \
            or rec["silent_hours"] > HEAVY_STALE_HOURS else VERDICT_OK
    if rec["rows"] == 0:
        return VERDICT_NEVER
    if rec["silent_hours"] is None:
        return VERDICT_NEVER
    limit = rec["threshold_hours"] or HEAVY_STALE_HOURS
    if rec["silent_hours"] < limit:
        return VERDICT_OK
    if rec["share_pct"] >= HEAVY_SHARE_PCT:
        return VERDICT_HEAVY_STALE
    return VERDICT_SILENT


# ── Почему источник молчит: все категории в одном месте ─────────────────────
#
# Списков было пять и жили они в трёх файлах: DEAD_SOURCES_WHITELIST здесь,
# KNOWN_RETIRED / KNOWN_EMPTY_OK / DEDUP_MIRRORS в freshness_watchdog,
# KNOWN_SILENT / KNOWN_TIGHTENED в funnel_watchdog. Смыслы у них РАЗНЫЕ, и
# сливать в один список нельзя — это прямо записано в их комментариях: retired
# значит «источника больше нет», mirror — «источник жив, но его вклад учтён под
# другим именем». Поэтому категории сохранены, а переехало только их место
# жительства: теперь любой сторож спрашивает `silence_excuse(name)` и получает
# и признак, и причину, вместо того чтобы заводить шестой список.

EXCUSE_WHITELIST = "whitelist"      # молчание объяснено и тревоги не требует
EXCUSE_RETIRED = "retired"          # источника больше нет
EXCUSE_EMPTY_OK = "empty_ok"        # канал почти не постит, ноль это норма
EXCUSE_MIRROR = "mirror"            # жив, но вклад учтён под другим именем
EXCUSE_ALERTS_OFF = "alerts_off"    # алерты выключены осознанно
EXCUSE_TIGHTENED = "tightened"      # алертов меньше из-за ужесточённого гейта

RETIRED_SOURCES = frozenset({
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
    # 05.08: отключён — в field_map не было external_id, адаптер подставлял
    # порядковый номер строки (0..9), поэтому источник физически не мог родить
    # новую строку. Плюс содержимое — биржевое табло спот-цен на цемент, без
    # заказчика и срока; 0 алертов за четыре месяца.
    "E-Birja встречный аукцион (листинг)",
    # 05.08: заменён на `E-Birja активные аукционы (xarid)` — у старого id лота
    # был позиционным, ссылка вела на чужой аукцион, срок не забирался.
    "Ebirja Аукционы",
})

# Enabled-источники, у которых 0 строк в БД может быть нормой (малоактивный
# upstream), — не алертить про отсутствие. Аудит 2026-06-11.

EMPTY_OK_SOURCES = frozenset({
    "TG: Фонд предпринимательства",  # канал почти не постит (последнее — 2024)
})


# Зеркала: источники, связанные общим `dedup_group` с другим источником, который
# опрашивается раньше. Их строки схлопываются cross-source дедупом, поэтому ноль
# новых строк у них — НОРМА, а не смерть. Держим включёнными как резерв: домены
# площадки падают порознь, и если ляжет основной, зеркало подхватит.
#
# Отдельным списком, а не в KNOWN_RETIRED, потому что смысл другой: retired —
# «источника больше нет», mirror — «источник жив, но его вклад учтён под другим
# именем». 28.04 общую группу уже разделяли, когда молчание проигравшего
# приняли за поломку; здесь оно ожидаемо и объяснено.
DEDUP_MIRRORS = frozenset({
    "Hayot Birja",  # зеркало xt-xarid.uz, тот же бэкенд, группа xtx-spa-tender
})



ALERTS_OFF_SOURCES = {
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
    # Разбор 11.08. Сторож три недели кричал «−66% алертов» и был прав по цифре,
    # но не знал про САМОЕ КРУПНОЕ осознанное изменение. XT-Xarid отключён от
    # алертов 01.07 (`_NO_PUSH_SOURCES`, commit c972851) как sell-side каталог:
    # покупателя нет, поля ставки в API нет, среди его алертов были собственные
    # лоты Winch. Замер по неделям: 127-187 алертов до 01.07 и ровно 0 после,
    # при том что строк источник даёт ВДВОЕ больше прежнего (27 тыс. → 58 тыс.
    # в неделю). Это примерно четверть всех июньских алертов, убранная нарочно.
    "XT-Xarid э-магазин":
        "sell-side каталог, отключён от алертов 01.07 (_NO_PUSH_SOURCES): был 26%+ "
        "алертов, включая собственные лоты Winch; строк даёт больше прежнего (11.08)",
}

# Источники, у которых поток алертов ОСЛАБЛЕН нарочно, но не обнулён. Молчанием
# это не считается, и в KNOWN_SILENT им не место — а объяснение нужно, иначе
# просадка объёма читается как поломка.
#
# Разбор 11.08: канал клиентских запросов до 13.07 уходил в алерты вообще без
# оценки (relevance_score пуст у всех строк до этой даты), потом на лиды повесили
# гейт анти-спама. Замер: строк на входе почти столько же (327 → 267 в неделю),
# алертов 228 → 79. То есть −149 алертов в неделю — это работа гейта, а не
# пропавший спрос.

TIGHTENED_SOURCES = {
    "TG: PR Media Group (запросы клиентов)":
        "с 13.07 лиды проходят гейт анти-спама: входящих строк столько же "
        "(327→267), алертов 228→79 — режет гейт, а не спрос (11.08)",
}


# Совместимость: сторожа импортируют привычные имена отсюда.
KNOWN_RETIRED = RETIRED_SOURCES
KNOWN_EMPTY_OK = EMPTY_OK_SOURCES


def silence_excuse(name):
    # type: (str) -> Optional[dict]
    """Почему молчание этого источника не поломка. None — значит поломка.

    Порядок проверки от самого сильного объяснения к самому слабому: «источника
    нет» перебивает «вклад учтён под другим именем», а то — «почти не постит».
    """
    if name in RETIRED_SOURCES:
        return {"category": EXCUSE_RETIRED, "reason": "источник выведен из эксплуатации"}
    if name in DEDUP_MIRRORS:
        return {"category": EXCUSE_MIRROR, "reason": "зеркало: вклад учтён под другим именем"}
    if name in EMPTY_OK_SOURCES:
        return {"category": EXCUSE_EMPTY_OK, "reason": "канал почти не постит"}
    if name in ALERTS_OFF_SOURCES:
        return {"category": EXCUSE_ALERTS_OFF, "reason": ALERTS_OFF_SOURCES[name]}
    if name in TIGHTENED_SOURCES:
        return {"category": EXCUSE_TIGHTENED, "reason": TIGHTENED_SOURCES[name]}
    if name in DEAD_SOURCES_WHITELIST:
        return {"category": EXCUSE_WHITELIST, "reason": DEAD_SOURCES_WHITELIST[name]}
    return None
