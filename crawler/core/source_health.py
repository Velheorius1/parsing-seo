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
    """
    if not rec["enabled"] or rec["excused"] or rec["muted_push"]:
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
