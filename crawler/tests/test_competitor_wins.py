"""Пины недельного разбора побед конкурентов (01.10).

Из чего выросло. Реестровый монитор считал конкурентом 18 ИНН из ручного
списка и видел 5 побед нашего профиля из всех за 30 дней; 28.09 нашёл «0 новых»
и промолчал. Ни одна победа не сшивалась с тем, где были мы: 512255 (журнал,
870 млн) мы алертили дважды — ни клика, выиграл OLTIN-NASHR за 620.

Свойства, которые тут держатся:
  • имя победителя НЕ судит профиль: фид кладёт его в search_text, и
    «OFSET-SURXON» протаскивал через ключевики оформление сцены (511179);
  • алерт — не доказательство профиля: UZEX идёт в обход AI, теплица ушла
    алертом с баллом 0; решает клик человека, потом сохранённый вердикт;
  • AI тогда сказал «наш», а алерта нет — это потеря доставки, не классификации;
  • типография определяется ТЕМ ЖЕ сопоставителем, что у гейта: «arakal»
    (Оракал) не засчитывается внутри «KARAKALPAK»;
  • скрытый победитель — не победа; сообщение строится и при нуле побед;
  • усечённый список помечен «…и ещё N», пропуски стоят раньше «алертили»;
  • окно кончается за 30 минут до «сейчас» (идущий краул), курсор после
    доставки уносит нерешённые в retry, повторная отправка за неделю — отказ;
  • «фид сделок просел» виден в заголовке, а не выглядит как «побед мало»;
второй выпуск (раз в 3 дня, два фида, список конкурентов):
  • итог ВМК-69 читается как сделка: победитель с ИНН, ссылка /civil-detail/<id>,
    а для гейта — лот раздела (source и тип подменены, победитель вырезан);
  • смена формата id 24.09 не удваивает итоги: 00-вариант и 05-вариант — один итог;
  • ритм 3 дня с допуском: запуск на 3 секунды раньше доставки не уезжает на 4-й день;
  • блок «Список» показывает и профильные, и «спрятанные» победы фирм списка,
    молчащих считает, не перечисляя; строка монитора — всегда, и об отказе тоже.

Run: python3 -m crawler.tests.test_competitor_wins   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta, timezone

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core import competitor_wins as CW  # noqa: E402

UUID_A = "57180d2a-fdd6-491c-914b-c5c5434604e8"
UUID_B = "674907c8-784d-4991-9bdb-33a6b04e698f"


def _deal(**over):
    row = {
        "id": UUID_B,
        "external_id": "512255",
        "source": "ETender Сделки (победители)",
        "source_url": "https://etender.uzex.uz/lot/512255",
        "title": "Nashrlarni chop etish xizmati",
        "organization": "ЎзР ИИВ Qalqon журнали",
        "search_text": "Nashrlarni chop etish xizmati ЎзР ИИВ Qalqon журнали OLTIN-NASHR MCHJ",
        "price": 620368000.0,
        "currency": "Сум",
        "created_at": "2026-09-30T12:03:34.963104+00:00",
        "extra_info": {
            "Цена старт": "870000000.0 Сум",
            "Победитель": "OLTIN-NASHR MCHJ (ИНН 306514938)",
            "Участников": "4",
            "Дата сделки": "2026-09-30T16:10:54",
        },
    }
    row.update(over)
    return row


class _V(object):
    """Минимальный ReplayVerdict для чистых функций."""

    def __init__(self, passed=True, stage=None, delivered=None, ai_error=False):
        self.passed_prefilter = passed
        self.dropped_at_stage = stage
        self.delivered = delivered
        self.ai_error = ai_error


# ── разбор сделки ────────────────────────────────────────────────────────────

def test_parse_win_reads_the_real_512255_deal():
    w = CW.parse_win(_deal())
    assert w["lot_key"] == "512255", w
    assert w["winner_inn"] == "306514938", w
    assert w["participants"] == 4, w
    assert w["start_price"] == 870000000.0 and w["start_currency"] == "Сум", w
    assert w["deal_date"] == "2026-09-30", w
    assert w["ours"] is False
    assert CW.discount_pct(w["start_price"], w["won_price"]) == 28.7


def test_hidden_winner_is_not_a_win():
    """«(ИНН )» — шаблон на пустых полях, а не победитель по имени «None»."""
    assert CW.parse_win(_deal(extra_info={"Победитель": "(ИНН )"})) is None
    assert CW.parse_win(_deal(extra_info={})) is None


def test_our_win_is_flagged_not_dropped():
    w = CW.parse_win(_deal(extra_info={"Победитель": "WINCH MCHJ (ИНН 123456789)"}))
    assert w is not None and w["ours"] is True


def test_clean_deal_row_strips_winner_from_gate_text():
    """511179: «ofset» из имени OFSET-SURXON пропускал оформление сцены."""
    row = _deal(title="Сахна безаш ишлари", organization="Сурхондарё хокимлиги",
                search_text="Сахна безаш ишлари Сурхондарё хокимлиги OFSET-SURXON MCHJ",
                extra_info={"Победитель": "OFSET-SURXON MCHJ (ИНН 300579386)"})
    clean = CW.clean_deal_row(row)
    assert "OFSET" not in clean["search_text"], clean["search_text"]
    assert clean["search_text"] == "Сахна безаш ишлари Сурхондарё хокимлиги"
    assert clean["extra_info"] == {}
    assert "OFSET" in row["search_text"], "исходная строка не должна меняться"


def test_start_price_garbage_is_none_not_zero():
    assert CW.parse_start_price({"Цена старт": "н/д"}) == (None, None)
    assert CW.parse_start_price({"Цена старт": "1750000.0 Доллар"}) == (1750000.0, "Доллар")
    assert CW.discount_pct(None, 5) is None
    assert CW.discount_pct(100, 120) is None, "цена сделки выше старта — не скидка"


def test_vmk69_is_not_a_lot_source():
    """Ссылки ВМК-69 — /civil-detail/, с /lot/ фида сделок не пересекаются."""
    assert "ETender Отбор (ВМК-69)" not in CW.LOT_SOURCES


# ── где были мы ──────────────────────────────────────────────────────────────

def test_alert_status_alerted_takes_first_alert():
    rows = [
        {"alert_seq": 8945, "source": "ETender UZEX", "created_at": "2026-09-22T12:02:38+00:00"},
        {"alert_seq": 8777, "source": "ETender Обсуждения", "created_at": "2026-09-18T06:02:59+00:00"},
    ]
    status, first = CW.alert_status(rows, {})
    assert status == CW.STATUS_ALERTED and first["alert_seq"] == 8777


def test_human_rejected_lot_is_skipped():
    rows = [{"alert_seq": 1, "source": "ETender UZEX"}, {"alert_seq": 2, "source": "ETender Обсуждения"}]
    assert CW.alert_status(rows, {1: "irrelevant", 2: "ad"})[0] == CW.STATUS_REJECTED_BY_HUMAN
    assert CW.alert_status(rows, {1: "irrelevant", 2: "client"})[0] == CW.STATUS_ALERTED


def test_missed_and_not_collected():
    assert CW.alert_status([{"alert_seq": None, "source": "ETender UZEX"}], {})[0] == CW.STATUS_MISSED
    assert CW.alert_status([], {})[0] == CW.STATUS_NOT_COLLECTED


def test_pick_lot_row_prefers_tender_then_freshest():
    rows = [
        {"id": "a", "source": "ETender Обсуждения", "collected_at": "2026-09-14"},
        {"id": "b", "source": "ETender UZEX", "collected_at": "2026-09-20"},
        {"id": "c", "source": "ETender UZEX", "collected_at": "2026-09-22"},
    ]
    assert CW.pick_lot_row(rows)["id"] == "c"
    assert CW.pick_lot_row([rows[0]])["id"] == "a"
    assert CW.pick_lot_row([]) is None


def test_alert_profile_human_then_stored_verdict_then_unknown():
    """Замер 01.10: 4 из 13 алерченных побед ушли алертом с AI-баллом 0."""
    junk = [{"alert_seq": 8836, "relevance_score": 0, "relevance_category": "irrelevant"}]
    real = [{"alert_seq": 8945, "relevance_score": 95, "relevance_category": "client"}]
    blank = [{"alert_seq": 8969, "relevance_score": None, "relevance_category": None}]
    assert CW.alert_profile(junk, {}) is False
    assert CW.alert_profile(real, {}) is True
    assert CW.alert_profile(blank, {}) is None, "нет вердикта — судит AI, а не догадка"
    assert CW.alert_profile(junk, {8836: "client"}) is True, "человек главнее машины"
    assert CW.alert_profile(junk + blank, {}) is None
    assert CW.alert_profile([{"alert_seq": 1, "relevance_score": 40}], {}) is False, "weak — не наш"


def test_missed_with_client_verdict_is_a_delivery_loss_not_a_gate_miss():
    lost = [{"alert_seq": None, "relevance_score": 90, "relevance_category": "client"}]
    rejected = [{"alert_seq": None, "relevance_score": 30, "relevance_category": "irrelevant"}]
    assert CW.missed_is_delivery_loss(lost) is True
    assert CW.missed_is_delivery_loss(rejected) is False
    assert CW.missed_is_delivery_loss([{"alert_seq": None}]) is False
    assert CW.stored_ai_then(lost + rejected) == 90
    assert CW.stored_ai_then([{"relevance_score": None}]) is None, "до AI не дошёл — не ноль"


def test_stage_and_profile_from_verdict():
    assert CW.stage_of(_V(passed=False, stage="no_keyword")) == "no_keyword"
    assert CW.stage_of(_V(passed=True, delivered=False)) == "ai"
    assert CW.stage_of(_V(passed=True, delivered=True)) is None
    assert CW.is_profile_verdict(_V(passed=True, delivered=True))
    assert not CW.is_profile_verdict(_V(passed=True, delivered=None)), "без AI профиль не известен"
    assert not CW.is_profile_verdict(_V(passed=True, delivered=True, ai_error=True)), \
        "сетевой сбой AI не делает победу нашей"
    assert not CW.is_profile_verdict(_V(passed=False, stage="min_price"))


def test_printer_like_uses_the_gates_own_matcher():
    """Совпадение считает переданный сопоставитель гейта; ИНН известной
    типографии засчитывается без него."""
    calls = []

    def hit(name):
        calls.append(name)
        return name.startswith("LABEL PRINT")
    label = CW.parse_win(_deal(extra_info={"Победитель": '"LABEL PRINT" MCHJ (ИНН 308380488)'}))
    label["winner"] = "LABEL PRINT MCHJ (ИНН 308380488)"
    assert CW.printer_like(label, [], hit) is True
    assert calls == ["LABEL PRINT MCHJ"], calls
    drill = CW.parse_win(_deal(extra_info={"Победитель": "ZVS DRILL SERVICE GROUP MCHJ (ИНН 1)"}))
    assert CW.printer_like(drill, [], lambda n: False) is False
    known = CW.parse_win(_deal())
    assert CW.printer_like(known, ["306514938"], lambda n: False) is True


def test_real_gate_matcher_does_not_see_oracal_inside_karakalpak():
    from crawler.core.notifier import _find_matching_keyword
    from crawler.core.tender_rows import row_to_raw_tender
    kws = ["arakal", "ofset", "pechat"]

    def hit(name):
        return _find_matching_keyword(row_to_raw_tender({"title": name}), kws) is not None
    assert not hit("ООО KARAKALPAK GAREZSIZ BAXALAU"), "стем не в начале слова — не совпадение"
    assert hit('"OFSET-SURXON" MCHJ')
    assert hit("ЧП PECHATNIK VOSTOKA")


# ── окно, курсор, здоровье фида ──────────────────────────────────────────────

NOW = datetime(2026, 10, 5, 10, 30, tzinfo=timezone.utc)


def test_window_first_run_is_seven_days_ending_before_the_crawl():
    start, end, clamped = CW.window(None, NOW)
    assert end == NOW - timedelta(minutes=30), end
    assert start == end - timedelta(days=7) and not clamped


def test_window_continues_from_cursor_without_gap():
    cursor = (NOW - timedelta(days=7, hours=3)).isoformat()
    start, _, clamped = CW.window(cursor, NOW)
    assert start.isoformat() == cursor and not clamped


def test_window_clamps_stale_cursor_and_says_so():
    start, end, clamped = CW.window((NOW - timedelta(days=60)).isoformat(), NOW)
    assert clamped and start == end - timedelta(days=21)


def test_window_ignores_cursor_in_future():
    start, end, clamped = CW.window((NOW + timedelta(days=1)).isoformat(), NOW)
    assert start == end - timedelta(days=7) and not clamped


def test_keyset_filter_quotes_timestamp():
    f = CW.keyset_filter("2026-09-30T12:03:34.963104+00:00", UUID_A)
    assert f == ('created_at.gt."2026-09-30T12:03:34.963104+00:00",'
                 'and(created_at.eq."2026-09-30T12:03:34.963104+00:00",id.gt.%s)' % UUID_A), f


def test_next_state_keeps_undecided_for_retry():
    end = NOW - timedelta(minutes=30)
    st = CW.next_state(end, 5, [UUID_A, None, UUID_B], NOW)
    assert st["cursor"] == end.isoformat() and st["retry"] == [UUID_A, UUID_B], st
    assert len(CW.next_state(end, 0, ["x%d" % i for i in range(500)], NOW)["retry"]) == CW.RETRY_CAP


def test_repeat_send_within_three_days_is_blocked():
    fresh = {"delivered_at": (NOW - timedelta(days=2)).isoformat()}
    old = {"delivered_at": (NOW - timedelta(days=4)).isoformat()}
    assert CW.repeat_blocked(fresh, NOW) is not None
    assert CW.repeat_blocked(old, NOW) is None
    assert CW.repeat_blocked(None, NOW) is None


def test_feed_health_flags_a_collapsed_feed():
    assert CW.feed_health(290, [300, 280, 310, 295]) == (297.5, False)
    med, dropped = CW.feed_health(40, [300, 280, 310, 295])
    assert dropped, "в семь раз меньше обычного — не «побед мало»"
    assert CW.feed_health(10, []) == (None, False)


# ── сообщение ────────────────────────────────────────────────────────────────

def _report(items=(), printers=(), **over):
    rep = {"start": NOW - timedelta(days=7), "end": NOW, "clamped": False, "items": list(items),
           "printers": list(printers), "ours": [],
           "coverage": {"deals": 290, "deals_median": 298, "hidden_winner": 3, "not_profile": 270,
                        "ai_used": 14, "ai_cap": 60}}
    rep.update(over)
    return rep


def _item(status, won=620368000.0, **win_over):
    win = CW.parse_win(_deal())
    win.update(win_over)
    win["won_price"] = won
    it = {"win": win, "status": status, "stage": None, "reason": None, "ai_then": None}
    if status == CW.STATUS_ALERTED:
        it["first_alert"] = {"alert_seq": 8777, "created_at": "2026-09-18T06:02:59+00:00",
                             "telegram_message_id": 555}
    return it


def test_message_is_built_even_with_zero_wins():
    text = CW.build_message(_report())
    assert "нет" in text and "Покрытие: сделок 290 (обычно ~298)" in text, text


def test_collapsed_feed_is_announced_in_the_header():
    rep = _report()
    rep["coverage"].update(deals=40, deals_median=298, feed_dropped=True)
    text = CW.build_message(rep)
    assert text.index("фид сделок, похоже, сломан") < text.index("Побед чужих"), text


def test_alerted_item_says_push_and_that_the_bid_is_not_marked():
    text = CW.build_message(_report([_item(CW.STATUS_ALERTED)]))
    assert "Алертили — забрали другие — 1" in text
    assert "алерт #8777 (пуш) · лот с 18.09 · заявка не отмечена" in text, text
    assert "870→620 млн (−28.7%)" in text and "4 уч." in text and "OLTIN-NASHR MCHJ" in text


def test_alerted_digest_and_bid_are_shown_as_such():
    it = _item(CW.STATUS_ALERTED)
    it["first_alert"]["telegram_message_id"] = None
    it["our_action"] = "bid"
    text = CW.build_message(_report([it]))
    assert "(дайджест)" in text and "подавали" in text and "не отмечена" not in text, text


def test_missed_reasons_are_distinct():
    loss = _item(CW.STATUS_MISSED)
    loss.update(reason=CW.REASON_DELIVERY_LOSS, ai_then=90)
    today = _item(CW.STATUS_MISSED, won=127e6)
    today.update(reason=CW.REASON_PASSES_TODAY, ai_then=None)
    text = CW.build_message(_report([loss, today]))
    assert "AI тогда: 90 — наш, но алерта не было" in text, text
    assert "до AI тогда не дошёл · сегодня гейт пропускает" in text, text


def test_missed_goes_before_alerted():
    items = [_item(CW.STATUS_ALERTED, won=900e6), _item(CW.STATUS_MISSED, won=691e6)]
    items[1].update(reason=CW.REASON_PASSES_TODAY)
    text = CW.build_message(_report(items))
    assert text.index("Наш профиль, но алерта не было") < text.index("Алертили"), text
    assert text.index("\n1. ") < text.index("сегодня гейт пропускает") < text.index("\n2. ")


def test_printer_section_names_todays_stage_and_the_shadow_hint():
    p = _item(CW.STATUS_MISSED, won=691e6, title="Poligrafiya mahsulotalrini ishlab chiqarish")
    p.update(status=CW.PRINTER, lot_status=CW.STATUS_MISSED, stage="no_keyword", ai_then=None)
    text = CW.build_message(_report(printers=[p]))
    assert "Спрятанные: выиграли конкуренты — гейт не узнал — 1" in text, text
    assert "гейт сегодня: нет ключевого слова · до AI тогда не дошёл" in text, text
    assert "shadow_search --add-keyword" in text
    assert "Лотов нашего профиля" not in text, "догадка не считается профилем"


def test_printers_never_seen_by_ai_go_first():
    """Замер 01.10: первой строкой шли автозапчасти (10 млрд, AI-балл 0),
    а «Poligrafiya…», которую AI не видел вовсе, — третьей."""
    parts = _item(CW.STATUS_MISSED, won=10272e6, title="Labo avtomobili butlovchi qismlari")
    parts.update(status=CW.PRINTER, lot_status=CW.STATUS_MISSED, stage="no_keyword", ai_then=0)
    poly = _item(CW.STATUS_MISSED, won=690e6, title="Poligrafiya mahsulotalrini")
    poly.update(status=CW.PRINTER, lot_status=CW.STATUS_MISSED, stage="no_keyword", ai_then=None)
    text = CW.build_message(_report(printers=[parts, poly]))
    assert text.index("Poligrafiya") < text.index("Labo avtomobili"), text


def test_alerted_section_is_capped_and_says_how_many_hidden():
    items = [_item(CW.STATUS_ALERTED, won=float(100 - i) * 1e6) for i in range(14)]
    text = CW.build_message(_report(items))
    assert text.count("алерт #8777") == 10, text.count("алерт #8777")
    assert "…и ещё 4 на" in text, text


def test_registry_winner_gets_a_star():
    text = CW.build_message(_report([_item(CW.STATUS_ALERTED)]), registry_inns=["306514938"])
    assert "OLTIN-NASHR MCHJ ★" in text


def test_html_is_escaped_and_plain_fallback_is_readable():
    it = _item(CW.STATUS_ALERTED, title="Бланки <b>A4</b> & конверты")
    text = CW.build_message(_report([it]))
    assert "&lt;b&gt;A4&lt;/b&gt; &amp; конверты" in text, text
    plain = CW.plain_text(text)
    assert "<b>" not in plain.replace("<b>A4</b>", "") and "Бланки <b>A4</b> & конверты" in plain, plain


def test_long_list_fits_telegram_and_is_marked():
    items = [_item(CW.STATUS_MISSED, won=float(10 + i) * 1e6,
                   title="Очень длинное название лота для проверки бюджета сообщения номер %d" % i,
                   customer="Заказчик с длинным названием организации %d" % i)
             for i in range(60)]
    for it in items:
        it.update(reason=CW.REASON_PASSES_TODAY)
    text = CW.build_message(_report(items))
    assert len(text) <= CW.TELEGRAM_LIMIT, len(text)
    assert "…и ещё" in text, "усечение должно быть видно"


def test_non_uzs_deal_is_shown_but_not_summed():
    it = _item(CW.STATUS_ALERTED, won=1750000.0, currency="Доллар", start_currency="Доллар")
    text = CW.build_message(_report([it]))
    assert "1.8 млн Доллар" in text, text
    assert "на 0 млн сум (+1 в другой валюте, в сумму не вошли)" in text, text


# ── итоги ВМК-69 ─────────────────────────────────────────────────────────────

def _civil(**over):
    row = {
        "id": UUID_A, "external_id": "result-26120500017591", "source": "UZEX Результаты",
        "source_url": "https://etender.uzex.uz/lot/26120500017591",
        "title": "Armiya jurnali 2026 yil 3-soni", "organization": "MUDOFAA VAZIRLIGI",
        "price": 283000000.0, "winning_price": 241000000.0, "currency": "UZS",
        "winner": "OLTIN-NASHR MCHJ (ИНН 306514938)", "status": "completed",
        "result_date": "2026-09-30T00:00:00", "message_type": "result",
        "created_at": "2026-09-30T12:03:34.963104+00:00",
    }
    row.update(over)
    return row


def test_parse_civil_win_reads_winner_prices_and_builds_the_working_link():
    w = CW.parse_civil_win(_civil())
    assert w["feed"] == CW.FEED_CIVIL and w["winner_inn"] == "306514938", w
    assert w["lot_key"] == "26120500017591", w
    assert w["source_url"] == "https://etender.uzex.uz/civil-detail/17591", w["source_url"]
    assert (w["start_price"], w["won_price"]) == (283000000.0, 241000000.0), w
    assert w["participants"] is None and w["deal_date"] == "2026-09-30", w
    assert CW.discount_pct(w["start_price"], w["won_price"]) == 14.8


def test_parse_civil_win_reads_the_legacy_inn_only_winner():
    w = CW.parse_civil_win(_civil(winner="ИНН: 204247640"))
    assert w["winner_inn"] == "204247640", w
    assert CW.winner_name(w["winner"]) == "ИНН 204247640"


def test_civil_hidden_winner_is_not_a_win():
    assert CW.parse_civil_win(_civil(winner=None)) is None
    assert CW.parse_civil_win(_civil(winner="  ")) is None
    assert CW.parse_civil_win(_civil(winner="(ИНН )")) is None
    assert CW.parse_civil_win(_civil(winner="None (ИНН None)")) is None


def test_civil_our_win_is_flagged():
    assert CW.parse_civil_win(_civil(winner="WINCH MCHJ (ИНН 123456789)"))["ours"] is True


def test_civil_gate_row_looks_like_our_vmk69_lot_not_like_a_result():
    row = _civil(search_text="Armiya jurnali OLTIN-NASHR", extra_info={"x": "y"})
    clean = CW.clean_row(CW.FEED_CIVIL, row)
    assert clean["source"] == "ETender Отбор (ВМК-69)" and clean["message_type"] == "tender", clean
    assert clean["winner"] is None and clean["extra_info"] == {} and clean["deadline"] is None
    assert "OLTIN" not in clean["search_text"], "имя победителя не судит профиль"
    assert clean["search_text"] == "Armiya jurnali 2026 yil 3-soni MUDOFAA VAZIRLIGI"
    assert row["message_type"] == "result", "исходная строка не должна меняться"
    assert CW.clean_row(CW.FEED_DEALS, _deal())["extra_info"] == {}


def test_civil_url_only_for_the_known_shape():
    assert CW.civil_detail_url("result-26120500018060") == "https://etender.uzex.uz/civil-detail/18060"
    assert CW.civil_detail_url("26120000010069") == "https://etender.uzex.uz/civil-detail/10069"
    assert CW.civil_detail_url("12345") is None
    assert CW.civil_detail_url("26110000010386") is None, "2611 — не ВМК-69: ссылки по формуле нет"


def test_format_switch_of_24_sep_does_not_double_a_result():
    old, new = "result-26120000010069", "result-26120500010069"
    assert CW.civil_norm_key(old) == CW.civil_norm_key(new)
    assert CW.civil_norm_key(old) != CW.civil_norm_key("result-26110000010069")
    rows = [{"external_id": old, "id": "a"}, {"external_id": new, "id": "b"},
            {"external_id": "result-26120500010070", "id": "c"}]
    assert [r["id"] for r in CW.dedupe_civil(rows)] == ["a", "c"]


# ── ритм раз в 3 дня ─────────────────────────────────────────────────────────

def test_cadence_survives_a_cron_that_fires_seconds_before_the_last_delivery_time():
    delivered = datetime(2026, 10, 2, 5, 0, 5, tzinfo=timezone.utc)
    state = {"delivered_at": delivered.isoformat()}
    three_days_early = delivered + timedelta(days=3) - timedelta(seconds=4)    # 05:00:01 на 3-й день
    assert CW.is_due(state, three_days_early) is True, "без допуска разбор уехал бы на 4-й день"
    assert CW.is_due(state, delivered + timedelta(days=1)) is False
    assert CW.is_due(state, delivered + timedelta(days=2, hours=1)) is False
    assert CW.is_due(state, delivered + timedelta(days=2, hours=23)) is True
    assert CW.is_due(None, NOW) is True


def test_cadence_constants_agree():
    assert CW.CADENCE == timedelta(days=3) and CW.MIN_REPEAT == CW.CADENCE - CW.DUE_SLACK


# ── блок «Список» и строка монитора ──────────────────────────────────────────

WATCH = {"306514938": "OLTIN-NASHR", "308044785": "PECHATNIK VOSTOKA", "203864183": "MATRIX"}


def _watch_item(status, inn, name, won, customer="Заказчик", **extra):
    it = _item(status, won=won, winner="%s MCHJ (ИНН %s)" % (name, inn), winner_inn=inn,
               start_price=won * 1.3, start_currency="Сум", customer=customer)
    it.update(extra)
    return it


def test_watch_block_counts_active_and_silent_firms_without_listing_the_silent():
    items = [_watch_item(CW.STATUS_ALERTED, "306514938", "OLTIN-NASHR", 620e6),
             _watch_item(CW.STATUS_ALERTED, "306514938", "OLTIN-NASHR", 100e6, customer="Другой")]
    text = CW.build_message(_report(items), watch=WATCH)
    assert "Список конкурентов (3)</b>: выиграли 1, молчат 2" in text, text
    assert "OLTIN-NASHR" in text and "×2" in text and "720 млн" in text, text
    assert "MATRIX" not in text, "молчащих не перечисляем"


def test_watch_block_includes_hidden_wins_of_the_list():
    hidden = _watch_item(CW.PRINTER, "308044785", "PECHATNIK VOSTOKA", 400e6, lot_status=CW.STATUS_MISSED)
    text = CW.build_message(_report(printers=[hidden]), watch=WATCH)
    assert "выиграли 1, молчат 2" in text and "гейт не узнал: 1" in text, text


def test_watch_block_ignores_firms_outside_the_list_and_missing_inn():
    other = _watch_item(CW.STATUS_ALERTED, "111111111", "STRANGER", 50e6)
    noinn = _watch_item(CW.STATUS_ALERTED, None, "NOINN", 50e6)
    text = CW.build_message(_report([other, noinn]), watch=WATCH)
    assert "выиграли 0, молчат 3" in text and "STRANGER" not in text.split("Лотов")[0]


def test_watch_block_is_capped_and_says_how_many_are_left():
    wide = {str(300000000 + i): "FIRM%d" % i for i in range(12)}
    items = [_watch_item(CW.STATUS_ALERTED, inn, name, 50e6) for inn, name in wide.items()]
    text = CW.build_message(_report(items), watch=wide)
    assert "выиграли 12, молчат 0" in text and "…и ещё 4 фирм" in text, text


def test_message_without_watch_is_unchanged():
    assert "Список конкурентов" not in CW.build_message(_report([_item(CW.STATUS_ALERTED)]))


def test_watch_map_takes_only_active_entities():
    reg = {"entities": [{"name": "A", "inn": "111111111"}, {"name": "NOINN"}],
           "separate_candidates": [{"name": "C", "inn": "222222222"}], "retired": [{"name": "R", "inn": "3"}]}
    assert CW.watch_map(reg) == {"111111111": "A"}


def test_monitor_line_for_every_outcome():
    ok = CW.summarize_monitor({"new_awards": [{"key": "k"}], "changed_awards": [], "bootstrap": False,
                               "sources": [{"source_id": "uzex_direct", "status": "complete"},
                                           {"source_id": "etender_deals", "status": "covered_by_digest"}]})
    assert ok["problems"] == [] and ok["new"] == 1
    assert "новых договоров 1 — пришли отдельным сообщением" in CW.build_message(_report(), monitor=ok)
    bad = CW.summarize_monitor({"sources": [{"label": "Cooperation contracts", "source_id": "c",
                                             "status": "collector_error"},
                                            {"label": "X", "source_id": "x", "status": "winner_unobservable"}]})
    assert bad["problems"] == ["Cooperation contracts: collector_error"], bad
    text = CW.build_message(_report(), monitor=bad)
    assert "новых договоров 0" in text and "не отработали: Cooperation contracts: collector_error" in text
    assert "⚠️ Монитор площадок не отработал: квитанции нет" in CW.build_message(
        _report(), monitor={"error": "квитанции нет"})
    assert "Монитор площадок" not in CW.build_message(_report()), "без параметра строки нет"


def test_bootstrap_monitor_says_baseline_not_zero_new():
    boot = CW.summarize_monitor({"bootstrap": True, "sources": []})
    assert "первый запуск" in CW.build_message(_report(), monitor=boot)


def test_civil_feed_collapse_is_announced_and_counted():
    rep = _report()
    rep["coverage"].update(civil=12, civil_median=70, civil_dropped=True)
    text = CW.build_message(rep)
    assert "итогов ВМК-69 в фиде 12 при обычных ~70" in text, text
    assert "итогов ВМК-69 12 (обычно ~70)" in text, text


def test_long_watch_block_still_fits_telegram():
    wide = {str(300000000 + i): "FIRM-WITH-A-LONG-NAME-%d" % i for i in range(30)}
    items = [_watch_item(CW.STATUS_ALERTED, inn, name, 500e6 - i * 1e6, customer="Очень длинное название заказчика %d" % i)
             for i, (inn, name) in enumerate(wide.items())]
    items += [_item(CW.STATUS_MISSED, won=900e6 - i) for i in range(30)]
    text = CW.build_message(_report(items), watch=wide, monitor={"error": "x" * 200})
    assert len(text) <= CW.TELEGRAM_LIMIT, len(text)
    assert "Список конкурентов (30)" in text


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
