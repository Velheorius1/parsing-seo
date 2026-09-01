"""Пины борьбы с шумом операционных алертов (22.08).

Из чего выросло. Данияр прислал скриншот канала: подряд пять сообщений
«Источник tg-* молчит 3 циклов», следом «Quality regression detected» с
`active_sources: 51.0 -> 2.0 [critical]` и списком NEW DEAD SOURCES сплошь из
`tg-*`, а в REVIVED — те же имена, что хоронил предыдущий прогон.

Разбор показал ДВЕ независимые причины, обе структурные:

1. База качества была ОДНА на все прогоны, а краулы ходят разными
   подмножествами: `0 */2` — только API-источники, `30 */2` — только Telegram.
   Каждый прогон сравнивался с базой чужого набора и рапортовал катастрофу.
   Замер по логу прода за сутки: 12 «Quality regression detected», ВСЕ в :30
   чётного часа, то есть ровно в telegram-прогон. Для `lite`-прогона такой
   guard уже стоял с той же формулировкой — «subset затёр бы baseline полного
   краула»; у второй пары его просто не сделали.

2. Порог «молчит 3 цикла» это ~6 часов тишины. Для площадки с ежечасными
   лотами осмысленно, для Telegram-канала, который пишет 0-1 раз в НЕДЕЛЮ, —
   гарантированная ложная тревога по расписанию.

Свойства, которые тут держатся:
  • подпись набора источников не зависит от порядка и различает наборы;
  • сравнение идёт с базой СВОЕГО набора, чужая база не читается;
  • поштучных отправок из краула больше нет — ни качества, ни «молчит»;
  • недельная доставка — ОДНИМ сообщением, а не пачкой на источник;
  • ежедневный канал для настоящей поломки (healthcheck) не тронут.

Run: python3 -m crawler.tests.test_alert_noise   (exit 1 on any failure)
"""
import io
import os
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
        supabase_url="", supabase_service_role_key="",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core.quality_tracker import _baseline_path, profile_signature

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


# --- подпись набора источников ----------------------------------------------

def test_signature_ignores_order():
    """Набор, а не список: порядок в YAML меняться не должен ломать базу."""
    assert profile_signature(["b", "a", "c"]) == profile_signature(["a", "b", "c"])


def test_api_and_telegram_runs_get_different_signatures():
    """ГЛАВНОЕ свойство. Пока подпись была общей, telegram-прогон сравнивался
    с базой API-прогона и давал `active_sources 51 -> 2` каждые два часа."""
    api = ["etender", "xarid-uzex", "cooperation-lots"]
    tg = ["tg-uzex", "tg-mitc", "tg-hamkorbank"]
    assert profile_signature(api) != profile_signature(tg)


def test_signature_is_stable_across_calls():
    a = ["etender", "tg-uzex"]
    assert profile_signature(a) == profile_signature(list(a))


def test_empty_source_set_has_its_own_bucket():
    assert profile_signature([]) == "empty"
    assert profile_signature(None) == "empty"


def test_baseline_file_differs_per_profile():
    api = _baseline_path(profile_signature(["etender"]))
    tg = _baseline_path(profile_signature(["tg-uzex"]))
    assert api != tg
    assert api.endswith(".json") and tg.endswith(".json")


def test_no_profile_keeps_the_historical_path():
    """Совместимость: старый файл базы не осиротеет."""
    assert _baseline_path(None).endswith("quality_baseline.json")
    assert _baseline_path("").endswith("quality_baseline.json")


# --- поштучных отправок больше нет ------------------------------------------

def test_crawler_no_longer_sends_quality_alerts_per_run():
    """Пин на источник: 12 ложных тревог в сутки уходили именно отсюда."""
    body = _src("crawler/core/runner.py")
    assert "send_quality_alert" not in body, "поштучная отправка качества вернулась"


def test_quality_regression_still_reaches_the_log():
    """Гасим ДОСТАВКУ, а не измерение: молча переставший считать сторож хуже
    шумного."""
    body = _src("crawler/core/runner.py")
    assert "Quality regression detected (profile=%s)" in body


def test_runner_compares_against_its_own_profile():
    body = _src("crawler/core/runner.py")
    assert "load_baseline(profile)" in body
    assert "save_snapshot(snapshot, profile)" in body


def test_silence_alerts_are_gated_weekly():
    """Раньше `alerts_to_send` уходили безусловно, каждый цикл."""
    body = _src("crawler/core/zero_result_tracker.py")
    i = body.index("    sent = 0")
    j = body.index("if alerts_to_send or recoveries_to_send:", i)
    block = body[i:j]
    assert "recovery_is_due()" in block, "недельный гейт не покрывает тревоги"
    assert "for body in bodies" not in block, "цикл поштучной отправки вернулся"


# --- одно сообщение вместо пачки --------------------------------------------

def test_weekly_digest_is_a_single_message_with_both_sections():
    from crawler.core.zero_result_tracker import _weekly_digest
    out = _weekly_digest(
        ["\U0001f507 Источник tg-uzex молчит 3 циклов (0 тендеров подряд)",
         "\U0001f507 Источник tg-mitc молчит 5 циклов (0 тендеров подряд)"],
        ["\U0001f514 Источник etender снова с данными (count=12)"])
    assert out.count("Молчат (2)") == 1
    assert out.count("Снова с данными (1)") == 1
    assert "tg-uzex" in out and "tg-mitc" in out and "etender" in out
    # именно ОДНО сообщение — заголовок ровно один
    assert out.count("Источники за неделю") == 1


def test_digest_survives_one_sided_input():
    from crawler.core.zero_result_tracker import _weekly_digest
    only_silent = _weekly_digest(["\U0001f507 Источник a молчит 3 циклов (x)"], [])
    only_back = _weekly_digest([], ["\U0001f514 Источник b снова с данными (count=1)"])
    assert "Молчат (1)" in only_silent and "Снова с данными" not in only_silent
    assert "Снова с данными (1)" in only_back and "Молчат" not in only_back


def test_digest_has_no_markdown_because_sender_has_no_parse_mode():
    """`_send_telegram` трекера шлёт без parse_mode — звёздочки и подчёркивания
    ушли бы в чат буквально. Первая редакция была с разметкой; поймано
    независимой проверкой до первого понедельника."""
    from crawler.core.zero_result_tracker import _weekly_digest
    out = _weekly_digest(["a молчит 3 циклов (x)"], ["b снова с данными (count=1)"])
    assert "*" not in out and "_" not in out, out
    src = _src("crawler/core/zero_result_tracker.py")
    i = src.index("async def _send_telegram")
    assert "parse_mode" not in src[i:i + 900], "если появился parse_mode — этот тест и сводку надо пересмотреть вместе"


def test_weekly_digest_is_built_from_state_not_from_this_run():
    """Пин на источник: сводка обязана собираться из pending-флагов всех
    источников, иначе тревога, пересёкшая порог не в понедельник, теряется."""
    src = _src("crawler/core/zero_result_tracker.py")
    i = src.index("    sent = 0")
    j = src.index("if alerts_to_send or recoveries_to_send:", i)
    block = src[i:j]
    assert "pending_alert" in block and "pending_recovery" in block
    assert "_weekly_digest(pend_alerts, pend_recov, standing)" in block
    assert "_weekly_digest(alerts_to_send" not in block, "сводка снова строится из переходов этого прогона"


def test_digest_points_at_the_daily_channel_for_real_breakage():
    """Иначе сводка читается как «сбор проверяется раз в неделю»."""
    from crawler.core.zero_result_tracker import _weekly_digest
    assert "healthcheck" in _weekly_digest(["x"], [])


# --- глушение хлопков Quality regression (01.09) -----------------------------
# База перезаписывается каждым краулем, и одиночный спад — шум выборки:
# 29 WARNING за неделю 25.08-01.09, ноль настоящих поломок (настоящие — etender
# 400 и прокси 402 — ловили healthcheck и руки). WARNING оставлен только
# устойчивому спаду: REGRESS_STREAK_WARN краулов подряд. Пустой краул вообще
# не сравнивается и базу не перезаписывает.


def test_regress_streak_counts_consecutive_and_resets():
    import json, shutil, tempfile
    from crawler.core import quality_tracker as qt
    tmp = tempfile.mkdtemp()
    saved = qt._LOG_DIR
    qt._LOG_DIR = tmp
    try:
        assert qt.bump_regress_streak("abc", True) == 1
        assert qt.bump_regress_streak("abc", True) == 2
        assert qt.bump_regress_streak("abc", False) == 0, "нормальный крауль сбрасывает серию"
        assert qt.bump_regress_streak("abc", True) == 1, "после сброса серия начинается заново"
        # профили не делят счётчик
        assert qt.bump_regress_streak("other", True) == 1
        # битый файл = 0, не вечный WARNING
        with open(qt._streak_path("abc"), "w") as f:
            f.write("мусор")
        assert qt.bump_regress_streak("abc", True) == 1
    finally:
        qt._LOG_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_crawl_does_not_become_baseline():
    """Снапшот без тендеров — «нет данных», базой быть не может."""
    import os, shutil, tempfile
    from crawler.core import quality_tracker as qt
    tmp = tempfile.mkdtemp()
    saved_dir, saved_log, saved_base = qt._LOG_DIR, qt._QUALITY_LOG, qt._BASELINE_FILE
    qt._LOG_DIR = tmp
    qt._QUALITY_LOG = os.path.join(tmp, "history.jsonl")
    qt._BASELINE_FILE = os.path.join(tmp, "base.json")
    try:
        snap = qt.QualitySnapshot.from_tenders([], source_stats={})
        qt.save_snapshot(snap, "abc", update_baseline=False)
        assert os.path.exists(qt._QUALITY_LOG), "история пишется всегда"
        assert not os.path.exists(qt._baseline_path("abc")), "база не тронута"
        qt.save_snapshot(snap, "abc")
        assert os.path.exists(qt._baseline_path("abc")), "по умолчанию база пишется"
    finally:
        qt._LOG_DIR, qt._QUALITY_LOG, qt._BASELINE_FILE = saved_dir, saved_log, saved_base
        shutil.rmtree(tmp, ignore_errors=True)


def test_runner_warns_only_on_sustained_regression():
    """Пины на runner: streak-гейт стоит, пустой краул не сравнивается."""
    body = _src("crawler/core/runner.py")
    assert "bump_regress_streak(profile, True)" in body
    assert "streak >= REGRESS_STREAK_WARN" in body, "WARNING без серии вернулся"
    assert "bump_regress_streak(profile, False)" in body, "сброс серии потерян"
    assert "update_baseline=False" in body, "пустой краул снова перетирает базу"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
