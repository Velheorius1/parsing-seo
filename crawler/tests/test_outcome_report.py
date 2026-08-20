"""Пины отчёта о воронке исхода (20.08).

Из чего выросло. Восемь наблюдательных контуров считали recall, precision,
маршрутизацию и свежесть источников — и все смотрели ВНУТРЬ механизма. Из-за
этого полгода шёл спор, 87% мусора или 43%, при полном незнании, принёс ли
хоть один алерт хоть один заказ.

Свойства, которые тут держатся:
  • «исход неизвестен» печатается ВСЕГДА. Отчёт без этой строки читается как
    «мы всё знаем», а реестра результатов нет ни у Cooperation, ни у
    XT-Xarid, ни у Telegram-каналов — это ~95% алертов;
  • неизвестное считается от ВСЕХ алертов месяца, а не от тех, по которым
    строка исхода вообще заведена: иначе отсутствие строки тихо выпадает из
    знаменателя и картина становится радужной сама собой;
  • месяц берётся у АЛЕРТА, не у исхода: вопрос «что принесли августовские
    алерты», а не «что закрылось в августе»;
  • номер строки в тексте и номер алерта в кнопке идут от ОДНОГО списка — та
    же ловушка, что уже ловилась в дайджесте 11.08: клик по строке 3 пометил
    бы чужой лот, и молча;
  • при нуле отметок «подал» отчёт прямо говорит, что отличить «работает
    вхолостую» от «участвую мимо системы» нечем.

Run: python3 -m crawler.tests.test_outcome_report   (exit 1 on any failure)
"""
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

from crawler.scripts.outcome_report import (
    NUDGE_SHOWN, build_nudge_keyboard, build_nudge_text, build_report_text,
    by_month, month_of, _top_winners,
)


def _a(seq, month, title="Лот"):
    return {"alert_seq": seq, "created_at": "%s-05T10:00:00+00:00" % month, "title": title}


# --- месяц ------------------------------------------------------------------

def test_month_of_normal_timestamp():
    assert month_of("2026-08-19T18:04:11+00:00") == "2026-08"


def test_month_of_garbage_is_none_not_a_bucket():
    """Выдуманный месяц смешал бы строки разных периодов в одну кучу."""
    for bad in (None, "", "2026", "xxxx-yy-zz", "20260819"):
        assert month_of(bad) is None, bad


# --- свод по месяцам --------------------------------------------------------

def test_month_comes_from_the_alert_not_from_the_outcome():
    """Июльский алерт, закрывшийся в августе, обязан считаться июльским."""
    alerted = [_a(1, "2026-07")]
    outcomes = [{"alert_seq": 1, "our_action": "bid", "lot_result": "won_by_us"}]
    rows = by_month(alerted, outcomes)
    assert len(rows) == 1 and rows[0]["month"] == "2026-07"
    assert rows[0]["won_by_us"] == 1


def test_alerts_without_an_outcome_row_stay_in_the_denominator():
    """ГЛАВНОЕ свойство. Считать неизвестное только по заведённым строкам —
    самый естественный способ отчитаться, что всё известно."""
    alerted = [_a(i, "2026-08") for i in range(1, 11)]
    outcomes = [{"alert_seq": 1, "our_action": "bid", "lot_result": "won_by_other"}]
    r = by_month(alerted, outcomes)[0]
    assert r["alerted"] == 10
    assert r["won_by_other"] == 1
    assert r["result_unknown"] == 9, r
    assert r["action_unknown"] == 9, r


def test_outcome_for_an_unknown_seq_is_dropped_not_guessed():
    alerted = [_a(1, "2026-08")]
    outcomes = [{"alert_seq": 999, "our_action": "bid", "lot_result": "won_by_us"}]
    r = by_month(alerted, outcomes)[0]
    assert r["won_by_us"] == 0 and r["result_unknown"] == 1


def test_months_are_sorted():
    rows = by_month([_a(1, "2026-08"), _a(2, "2026-06"), _a(3, "2026-07")], [])
    assert [r["month"] for r in rows] == ["2026-06", "2026-07", "2026-08"]


# --- текст отчёта -----------------------------------------------------------

def test_unknown_line_is_always_printed():
    rows = by_month([_a(1, "2026-08")], [])
    assert "Исход неизвестен" in build_report_text(rows)


def test_zero_bids_says_what_cannot_be_told_apart():
    """Состояние на 20.08. Отчёт обязан назвать это прямо, а не показать ноль
    в таблице и промолчать."""
    txt = build_report_text(by_month([_a(1, "2026-08")], []))
    assert "вхолостую" in txt and "мимо системы" in txt


def test_warning_disappears_once_there_is_at_least_one_bid():
    outcomes = [{"alert_seq": 1, "our_action": "bid", "lot_result": None}]
    txt = build_report_text(by_month([_a(1, "2026-08")], outcomes))
    assert "вхолостую" not in txt
    assert "Взялись: *1*" in txt


def test_empty_period_does_not_divide_by_zero():
    assert "Нет алертов" in build_report_text([])


def test_top_winners_counts_only_named_competitors():
    """Проигрыш без имени победителя — знание о себе, а не о рынке; попадать в
    список «кто забирал наши лоты» он не должен."""
    rows = [
        {"lot_result": "won_by_other", "winner": "ЧП A"},
        {"lot_result": "won_by_other", "winner": "ЧП A"},
        {"lot_result": "won_by_other", "winner": None},
        {"lot_result": "no_deal", "winner": "ЧП B"},
        {"lot_result": "won_by_us", "winner": "ВИНЧ"},
    ]
    assert _top_winners(rows) == [("ЧП A", 2)]


# --- вопрос про исход -------------------------------------------------------

def _pending(n):
    return [{"alert_seq": 100 + i} for i in range(n)]


def test_nudge_button_number_matches_the_line_number():
    """Та же ловушка, что ловилась в дайджесте 11.08: разъехавшись, номер
    строки и номер алерта пометили бы чужой лот, и по логам это не видно."""
    rows = _pending(4)
    kb = build_nudge_keyboard(rows)["inline_keyboard"]
    text = build_nudge_text(rows, {100 + i: "Лот %d" % i for i in range(4)})
    for i, row in enumerate(kb, 1):
        seq = 100 + i - 1
        assert all(b["callback_data"].startswith("out:%d:" % seq) for b in row), row
        assert row[0]["text"].startswith("%d " % i)
        assert ("*%d.* #%03d" % (i, seq)) in text


def test_nudge_offers_a_cancelled_option():
    """Без неё «не взяли» пришлось бы жать и на отменённый лот — то есть
    записывать несуществующего победителя."""
    kb = build_nudge_keyboard(_pending(1))["inline_keyboard"]
    labels = [b["callback_data"].rsplit(":", 1)[-1] for b in kb[0]]
    assert labels == ["won", "lost", "dead"]


def test_nudge_is_capped():
    """Больше десятка строк в одном сообщении — и человек не отвечает вовсе;
    ровно это уже случилось с дайджестом."""
    kb = build_nudge_keyboard(_pending(30))["inline_keyboard"]
    assert len(kb) == NUDGE_SHOWN


def test_nudge_survives_a_missing_title():
    text = build_nudge_text(_pending(1), {})
    assert "#100" in text


# --- имя победителя в разметке ---------------------------------------------

def test_winner_name_is_cut_by_word_not_mid_paren():
    """Слепой срез по 44 давал «OOO "PREMIUM POLIGRAF BIZNES" (ИНН 303018986»
    — незакрытая скобка. С * или _ то же самое уронило бы разметку ВСЕГО
    сообщения, а не одной строки."""
    from crawler.scripts.outcome_report import _short
    out = _short('OOO "PREMIUM POLIGRAF BIZNES" (ИНН 303018986)')
    assert not out.endswith("("), out
    assert out.count("(") == out.count(")"), out


def test_winner_name_drops_markdown_active_characters():
    from crawler.scripts.outcome_report import _short
    out = _short("ЧП *A* _B_ `C` [D]")
    assert not any(ch in out for ch in "*_`[]"), out


def test_uzbek_apostrophe_is_converted_not_deleted():
    """FARG`ONA / G`ULOM — обратный апостроф часть имени, но в Telegram он
    открывает code-span и рвёт разметку всего сообщения. Выбросить его —
    исказить имя, оставить — сломать отчёт."""
    from crawler.scripts.outcome_report import _short
    assert _short("FARG`ONA KITOB") == "FARG'ONA KITOB"


def test_short_name_passes_through_untouched():
    from crawler.scripts.outcome_report import _short
    assert _short("ЧП A") == "ЧП A"


def test_report_reads_all_alerts_not_the_first_page():
    """Пин на источник: первый прогон 20.08 показал 1000 алертов вместо 7553."""
    import io as _io, os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    src = _io.open(_os.path.join(_os.path.dirname(here), "scripts",
                                 "outcome_report.py"), encoding="utf-8").read()
    i = src.index("def _alerted_rows")
    j = src.index("def _top_winners")
    assert "iter_by_seq" in src[i:j], "отчёт снова читает одной страницей"
    assert "limit(20000)" not in src, "вернулся limit, который PostgREST игнорирует"


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
