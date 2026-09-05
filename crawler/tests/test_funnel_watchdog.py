"""Пины сторожа воронки (30.07).

Сторож нужен ровно потому, что за пять недель поток алертов упал 84 → 14 в день
и этого никто не заметил. Значит главное свойство — он должен звенеть на таком
падении и молчать на спокойных данных. Тесты держат это на синтетике: чистая
арифметика вердикта, без БД и без сети.

Run: python3 -m crawler.tests.test_funnel_watchdog   (exit 1 on any failure)
"""
import sys
import types

from crawler.tests._stubs import install_stub


def _load():
    """Импорт сторожа с заглушками прод-зависимостей (как в test_scout_store_roundtrip):
    pydantic_settings и Telegram-креды в тестовой среде отсутствуют."""
    install_stub("crawler.auth.session_store",
                 session_store=types.SimpleNamespace(get_setting=lambda k: None,
                                                     set_setting=lambda k, v: True))
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(telegram_bot_token="",
                                           telegram_alert_chat_id="")
        sys.modules[cfg] = m
    import crawler.scripts.funnel_watchdog as W
    return W


W = _load()
DAYS_BASE = W.DAYS_BASE
DAYS_RECENT = W.DAYS_RECENT
MAX_SILENT_ALARMS = W.MAX_SILENT_ALARMS
_delta = W._delta
_pct_drop = W._pct_drop
render = W.render
silent_sources = W.silent_sources
verdict = W.verdict


def _rep(per_base=54.0, per_recent=50.0, stages=None, silent=None, base_days=28,
         slide=None, weekly=None):
    return {
        "as_of": "2026-07-30T00:00:00+00:00",
        "base_start": "2026-06-25T00:00:00+00:00",
        "recent_start": "2026-07-23T00:00:00+00:00",
        "days_with_runs_in_base": base_days,
        "alerts_base": int(per_base * DAYS_BASE),
        "alerts_recent": int(per_recent * DAYS_RECENT),
        "per_day_base": per_base, "per_day_recent": per_recent,
        "volume_drop_pct": _pct_drop(per_base, per_recent),
        "stages": stages if stages is not None else [
            {"name": "алертов / новых", "base": 0.03, "recent": 0.03,
             "alarm": True, "drop": 0.0},
        ],
        "silent_sources": silent or [],
        "weekly": weekly or [], "slide": slide,
        "src_base": {}, "src_recent": {},
    }


# ── объём ─────────────────────────────────────────────────────────────────────

def test_quiet_data_is_silent():
    alarms, _notes = verdict(_rep(per_base=54.0, per_recent=50.0))
    assert alarms == [], alarms


def test_july_collapse_fires():
    # Ровно тот случай, который сторож пропустил в реальности.
    alarms, _ = verdict(_rep(per_base=54.0, per_recent=18.6))
    assert any("объём" in a for a in alarms), alarms


def test_absolute_floor_fires_even_without_drop():
    # Поток был низким и остался низким: относительного падения нет, но 3 алерта
    # в день — это уже неработающая система.
    alarms, _ = verdict(_rep(per_base=3.2, per_recent=3.0))
    assert any("ниже пола" in a for a in alarms), alarms


def test_growth_does_not_fire():
    alarms, _ = verdict(_rep(per_base=20.0, per_recent=40.0))
    assert alarms == [], alarms


def test_short_base_refuses_to_judge():
    alarms, notes = verdict(_rep(per_base=54.0, per_recent=1.0, base_days=5))
    assert alarms == []
    assert any("база короткая" in n for n in notes), notes


# ── сползание ─────────────────────────────────────────────────────────────────

def _bk(values):
    """Недельные корзины из значений алертов/день, от старой к свежей."""
    return [{"from": "2026-06-%02d" % (1 + 7 * i), "to": "2026-06-%02d" % (8 + 7 * i),
             "alerts": int(v * 7), "days": 7, "per_day": v}
            for i, v in enumerate(values)]


def test_slide_catches_five_week_decay():
    # Настоящая кривая июля: 84 → 79 → 49 → 41 → 18 алертов/день.
    sl = W.trend_slide(_bk([84.0, 79.0, 49.0, 41.0, 18.0]))
    assert sl is not None and sl["drop"] > 30, sl


def test_slide_fires_in_mid_july_when_percent_check_still_silent():
    # На 12.07 сравнение с базой давало −39.6% и молчало (порог 50%), а форма
    # кривой уже была сползанием. Ради этого проверка и добавлена.
    path = [82.0, 70.0, 60.0, 50.0]
    # Процентная проверка тут по построению молчит: −39% при пороге 50%.
    assert _pct_drop(82.0, 50.0) < 50
    sl = W.trend_slide(_bk(path))
    assert sl is not None, sl
    alarms, _ = verdict(_rep(per_base=82.0, per_recent=50.0, stages=[]))
    assert alarms == [], "без проверки на форму кривой тут тихо — это и есть дыра"
    alarms2, _ = verdict(_rep(per_base=82.0, per_recent=50.0, slide=sl))
    assert any("сползание" in a for a in alarms2), alarms2


def test_one_bad_week_is_not_a_slide():
    # Разброс: провал и отскок. Звенеть тут значит звенеть на шуме.
    assert W.trend_slide(_bk([60.0, 40.0, 62.0, 58.0])) is None


def test_gentle_decline_below_threshold_is_not_a_slide():
    # Три шага вниз, но суммарно −10%: это дыхание, а не сползание.
    assert W.trend_slide(_bk([50.0, 48.0, 46.0, 45.0])) is None


def test_recovery_breaks_the_slide():
    assert W.trend_slide(_bk([84.0, 60.0, 40.0, 55.0])) is None


def test_slide_needs_enough_weeks_with_data():
    assert W.trend_slide(_bk([84.0, 40.0])) is None


# ── стадии ────────────────────────────────────────────────────────────────────

def test_end_to_end_stage_drop_fires():
    st = [{"name": "алертов / новых", "base": 0.030, "recent": 0.008,
           "alarm": True, "drop": 73.3}]
    alarms, _ = verdict(_rep(stages=st))
    assert any("алертов / новых" in a for a in alarms), alarms


def test_informational_stage_never_fires():
    # «AI / новых» справочная: счётчик ai_calls_count смешивает обогащение и
    # релевантность, звенеть на нём — звенеть на неоднозначности.
    st = [{"name": "AI / новых", "base": 0.030, "recent": 0.001,
           "alarm": False, "drop": 96.7}]
    alarms, _ = verdict(_rep(stages=st))
    assert alarms == [], alarms


# ── источники ─────────────────────────────────────────────────────────────────

def test_silent_source_needs_minimum_history():
    out = silent_sources({"Мелкий": 2}, {}, nopush=frozenset())
    assert out == [], out


def test_silent_source_detected():
    out = silent_sources({"Живой": 40}, {}, nopush=frozenset())
    assert len(out) == 1 and out[0]["was"] == 40
    assert out[0]["excused"] is None


def test_source_still_alerting_is_not_silent():
    out = silent_sources({"Живой": 40}, {"Живой": 3}, nopush=frozenset())
    assert out == [], out


def test_deliberately_demoted_source_is_excused():
    out = silent_sources({"XT-Xarid э-магазин": 215}, {},
                         nopush=frozenset({"XT-Xarid э-магазин"}))
    assert out[0]["excused"] == "снят с push в коде"
    alarms, notes = verdict(_rep(silent=out))
    assert alarms == [], alarms
    assert any("объяснимо" in n for n in notes), notes


def test_known_silent_news_channel_is_excused():
    out = silent_sources({"TG: Хамкорбанк": 19}, {}, nopush=frozenset())
    assert out[0]["excused"], out
    alarms, _ = verdict(_rep(silent=out))
    assert alarms == [], alarms


def test_many_silent_sources_collapse_into_one_line():
    src = dict(("Источник %d" % i, 10 + i) for i in range(MAX_SILENT_ALARMS + 3))
    out = silent_sources(src, {}, nopush=frozenset())
    alarms, _ = verdict(_rep(silent=out))
    assert len(alarms) == MAX_SILENT_ALARMS + 1, alarms
    assert any("ещё 3 источника" in a for a in alarms), alarms


# ── состояние: когда молчать, когда писать снова ──────────────────────────────

def test_alarm_kind_is_stable_when_only_numbers_drift():
    # Иначе сторож писал бы в Telegram каждый день, пока просадка не пройдёт.
    a = "объём: 54.0 → 18.6 алертов/день (−65.6% к базе 28 дн)"
    b = "объём: 53.1 → 18.9 алертов/день (−64.4% к базе 28 дн)"
    assert W.alarm_kind(a) == W.alarm_kind(b) == "volume"


def test_alarm_kinds_separate_by_meaning():
    assert W.alarm_kind("объём ниже пола: 3.0 алертов/день (пол 8.0)") == "floor"
    assert W.alarm_kind("сползание: 3 недели подряд вниз, 84.0 → 18.0") == "slide"
    assert W.alarm_kind("стадия «алертов / новых»: 0.030 → 0.008 (−73.3%)") \
        == "stage:алертов / новых"
    assert W.alarm_kind("источник замолчал в алертах: «TG: Мин ИТ» (было 16, стало 0)") \
        == "source:TG: Мин ИТ"
    assert W.alarm_kind("ещё 3 источника замолчали (30 алертов в базе): A, B, C") \
        == "sources_tail"


def test_new_source_going_silent_is_a_new_kind():
    k1 = W.alarm_kind("источник замолчал в алертах: «A» (было 16, стало 0)")
    k2 = W.alarm_kind("источник замолчал в алертах: «B» (было 16, стало 0)")
    assert k1 != k2


# ── подписи ───────────────────────────────────────────────────────────────────

def test_growth_is_not_printed_as_double_minus():
    assert _delta(-85.5) == " (+85.5% роста)"
    assert _delta(65.6) == " (−65.6%)"
    assert _delta(None) == ""


def test_render_marks_healthy_and_broken():
    ok = render(_rep(), [], [])
    assert "в норме" in ok
    bad = render(_rep(per_recent=10.0), ["объём: упал"], [])
    assert "просела" in bad and "объём: упал" in bad


# ── объяснения к просадке объёма (разбор 11.08) ──────────────────────────────

def test_volume_alarm_carries_the_known_causes():
    """Сторож кричал «−66% алертов» с 14.07 и был прав по цифре. Причины лежали
    в коммитах (01.07 отключение sell-side каталога, 13.07 гейт на лиды, 30.07
    мьюты новостных каналов), но рядом с тревогой их не было — и три недели
    никто их не свёл. Теперь тревога приходит вместе с уже разобранным."""
    alarms, notes = verdict(_rep(per_base=54.0, per_recent=12.0))
    assert any("объём:" in a for a in alarms), alarms
    joined = " | ".join(notes)
    assert "часть падения объяснена" in joined, notes
    assert "PR Media Group" in joined, notes


def test_explanations_do_not_silence_the_alarm():
    """Ключевое: объяснение — это подпись под тревогой, а не её отмена.
    Иначе настоящая регрессия спрячется за списком старых причин."""
    alarms, _notes = verdict(_rep(per_base=54.0, per_recent=12.0))
    assert any("−77.8%" in a or "объём:" in a for a in alarms), alarms


def test_no_explanations_when_volume_is_fine():
    """Пояснения не должны сыпаться в спокойный отчёт."""
    _alarms, notes = verdict(_rep(per_base=54.0, per_recent=50.0))
    assert not any("часть падения объяснена" in n for n in notes), notes


def test_every_excuse_names_a_date_and_a_measurement():
    """Список объяснений — не свалка: без даты и цифры он глушит сторожа.
    Правило записано в шапке KNOWN_SILENT, тут оно проверяется."""
    import re
    for table in (W.KNOWN_SILENT, W.KNOWN_TIGHTENED):
        for src, why in table.items():
            assert re.search(r"\d{2}\.\d{2}", why), (src, why)
            assert re.search(r"\d", why), (src, why)



if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)


# --- метрика объёма: вся доставка, а не только пуши (05.09) -------------------

def test_count_alerted_counts_rows_in_the_window():
    """`crawl_runs.alerts_sent` считает только пуши основного прохода: дайджест
    и досылка recheck в него не попадают. Замер за неделю дал 90 против 357 по
    alert_seq — сторож мерил четверть собственной доставки."""
    rows = [{"collected_at": "2026-08-01T10:00:00", "alert_seq": 1},
            {"collected_at": "2026-08-05T10:00:00", "alert_seq": 2},
            {"collected_at": "2026-08-09T10:00:00", "alert_seq": 3}]
    assert W.count_alerted(rows, "2026-08-01", "2026-08-08") == 2
    assert W.count_alerted(rows, "2026-08-08", "2026-08-20") == 1
    assert W.count_alerted([], "2026-08-01", "2026-08-20") == 0


def test_count_alerted_survives_broken_timestamps():
    rows = [{"collected_at": None}, {"alert_seq": 5}, {"collected_at": "мусор"}]
    assert W.count_alerted(rows, "2026-08-01", "2026-08-20") == 0


def test_weekly_buckets_prefer_delivery_over_pushes():
    """Корзины тренда считают ту же доставку, иначе тренд и объём мерили бы
    разные вещи и расходились."""
    end = W.datetime(2026, 8, 15, tzinfo=W.timezone.utc)
    runs = [{"started_at": "2026-08-10T00:00:00+00:00", "alerts_sent": 5}]
    alerted = [{"collected_at": "2026-08-10T00:00:00+00:00", "alert_seq": i}
               for i in range(20)]
    with_delivery = W.weekly_buckets(runs, end, 2, alerted)
    without = W.weekly_buckets(runs, end, 2)
    assert sum(b["alerts"] for b in with_delivery) == 20
    assert sum(b["alerts"] for b in without) == 5, "старое поведение без выборки"
