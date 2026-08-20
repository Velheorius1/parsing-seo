"""Пины кнопок исхода (20.08).

Из чего выросло. Разбор месяц-к-месяцу: 7553 алерта за полгода, и ни одной
записи о том, взялись ли мы хоть за один. Кнопки на алерте были только про
релевантность («интересно / реклама / не моё»), то есть система могла узнать,
хорош ли лот, и не могла узнать, случилось ли что-нибудь.

Ось исхода ДРУГАЯ, чем ось релевантности, и это главное свойство файла: лот
бывает идеально релевантным и при этом проигранным. Поэтому у пуш-алерта
теперь ДВЕ строки кнопок на один и тот же номер, и клик по одной не смеет
гасить другую — иначе, отметив «интересно», теряешь возможность сказать, что
подал заявку.

Свойства, которые тут держатся:
  • у пуша две строки: fb: (релевантность) и out: (исход);
  • клик по любой из них оставляет вторую живой;
  • значения OUTCOME_MAP лежат внутри CHECK-ограничений миграции 023 — иначе
    запись падает на проде, а бот отвечает «ошибка записи» без объяснений;
  • дайджест кнопку исхода НЕ получает (десять строк, действуем по пушам);
  • разбор callback_data не падает на мусоре из чужих сообщений.

Run: python3 -m crawler.tests.test_outcome_buttons   (exit 1 on any failure)
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

from crawler.core import notifier as N
from crawler.core.outcome import RESULTS, ACTIONS
import crawler.scripts.feedback_bot as B

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


# --- сама кнопка ------------------------------------------------------------

def test_outcome_row_is_one_button_with_its_own_prefix():
    row = N._build_outcome_row(42)
    assert len(row) == 1
    assert row[0]["callback_data"] == "out:42:bid"


def test_wording_differs_for_leads_but_the_axis_is_one():
    """Заявку ПОДАЮТ на тендер, лид БЕРУТ в работу. Callback обязан совпасть:
    вопрос один — взялись или нет."""
    tender = N._build_outcome_row(7, False)[0]
    lead = N._build_outcome_row(7, True)[0]
    assert tender["text"] != lead["text"]
    assert tender["callback_data"] == lead["callback_data"] == "out:7:bid"


def test_push_alert_actually_sends_both_rows():
    """Пин на место сборки: кнопка, которую никто не прикрепил к сообщению,
    полезна ровно настолько же, насколько её отсутствие."""
    src = _src("crawler/core/notifier.py")
    assert '"inline_keyboard": [_fb_row, _build_outcome_row(seq, _is_tg_lead(tender))]' in src


def test_digest_stays_without_outcome_buttons():
    """Десять строк по три кнопки — частокол. Действуем по пушам."""
    kb = N._build_digest_keyboard(
        [], 1)["inline_keyboard"]
    assert kb == []
    src = _src("crawler/core/notifier.py")
    i = src.index("def _build_digest_keyboard")
    j = src.index("async def _send_digest", i)
    assert "out:" not in src[i:j]


# --- клик по одной оси не гасит другую --------------------------------------

def _push_keyboard(seq):
    fb = [{"text": "✅", "callback_data": "fb:%d:ok" % seq},
          {"text": "❌", "callback_data": "fb:%d:skip" % seq}]
    return [fb, N._build_outcome_row(seq)]


def test_clicking_relevance_keeps_the_outcome_button_alive():
    """ГЛАВНОЕ свойство. Раньше любой клик схлопывал всю клавиатуру в одну
    подпись — с двумя осями это означало бы, что оценив релевантность, ты
    больше не можешь сказать, что подал заявку."""
    rows = _push_keyboard(500)
    out = B.remaining_keyboard(rows, 500, B.LABEL_MAP["ok"], prefix="fb")
    assert len(out) == 2
    assert out[0][0]["callback_data"] == "done"
    assert out[1][0]["callback_data"] == "out:500:bid"


def test_clicking_outcome_keeps_the_relevance_buttons_alive():
    rows = _push_keyboard(501)
    out = B.remaining_keyboard(rows, 501, B.OUTCOME_MAP["bid"], prefix="out")
    assert len(out) == 2
    assert [b["callback_data"] for b in out[0]] == ["fb:501:ok", "fb:501:skip"]
    assert out[1][0]["callback_data"] == "done"


def test_default_prefix_is_unchanged_for_old_callers():
    """Дайджест зовёт без префикса — прежнее поведение обязано сохраниться."""
    rows = [[{"text": "x", "callback_data": "fb:9:ok"}]]
    assert B.remaining_keyboard(rows, 9, B.LABEL_MAP["ok"])[0][0]["callback_data"] == "done"


def test_second_outcome_click_is_idempotent():
    rows = _push_keyboard(502)
    once = B.remaining_keyboard(rows, 502, B.OUTCOME_MAP["bid"], prefix="out")
    twice = B.remaining_keyboard(once, 502, B.OUTCOME_MAP["bid"], prefix="out")
    assert twice == once


# --- разбор callback --------------------------------------------------------

def test_parse_reads_both_families():
    assert B.parse_callback("out:123:bid") == ("out", 123, "bid")
    assert B.parse_callback("fb:7:skip") == ("fb", 7, "skip")


def test_parse_rejects_garbage_without_raising():
    for bad in ("done", "", None, "out:bid", "out:x:bid", "xx:1:bid", "out:1:bid:extra"):
        assert B.parse_callback(bad) == (None, None, None), bad


# --- согласованность со схемой ---------------------------------------------

def test_outcome_map_values_fit_the_migration_check():
    """Значение вне CHECK падает уже на проде, и человек видит только
    «ошибка записи» — тратя единственный клик, который он вообще сделал."""
    mig = _src("supabase/migrations/023_alert_outcome.sql")
    for info in B.OUTCOME_MAP.values():
        assert info["value"] in (RESULTS + ACTIONS), info
        assert "'%s'" % info["value"] in mig, info["value"]


def test_action_and_result_buttons_go_to_different_columns():
    """Кнопка «подал» пишет НАШЕ действие, кнопки результата — судьбу лота.
    Перепутать колонки — записать «мы выиграли» в графу «мы подали»."""
    assert B.OUTCOME_MAP["bid"]["kind"] == "action"
    for k in ("won", "lost", "dead"):
        assert B.OUTCOME_MAP[k]["kind"] == "result"


def test_lost_does_not_claim_a_named_competitor():
    """«Не взяли» — знание о СЕБЕ. Кто выиграл, человек знать не обязан, и
    winner остаётся пустым: пустое имя и отличает ручное «мы проиграли» от
    разобранного автоматикой «выиграл вот этот»."""
    src = _src("crawler/scripts/feedback_bot.py")
    i = src.index("def process_outcome")
    j = src.index("\ndef ", i + 10)
    assert "record_result(seq, info[\"value\"])" in src[i:j]
    assert "winner=" not in src[i:j]


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
