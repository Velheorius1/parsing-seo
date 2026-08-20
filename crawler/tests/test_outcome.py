"""Пины учёта исходов алертов (20.08).

Из чего выросло. Разбор месяц-к-месяцу 20.08: 7553 алерта за полгода и ноль
записей о том, чем они кончились. `winner` заполнен у 2963 строк — и ни у
одной алерченной: их пишет results_tracker из CivilContracts (прямые
договоры, id-пространство `2612 0000 <contract>`), а наши лоты живут в
`2612 0012 <lot>`. Пересечься они не могли никогда.

При этом исход 74% алерченных etender-лотов ЛЕЖАЛ В НАШЕЙ ЖЕ БАЗЕ несшитым
(замер 20.08: 177 из 361 в фиде сделок, ещё 91 в фиде несостоявшихся) —
победитель приходит в extra_info->>'Победитель', мимо колонки winner.

Свойства, которые тут держатся:
  • сделка БЕЗ раскрытого победителя — это не «не разыгран»: торги
    состоялись, мы просто не видим кто выиграл. Записать 'no_deal' значило бы
    придумать себе знание;
  • ручной исход автоматика не перетирает НИКОГДА — иначе ночной прогон молча
    стирает правку Данияра;
  • повторный прогон с тем же ответом ничего не пишет, иначе updated_at
    дёргается каждую ночь и «свежесть исхода» перестаёт что-либо значить;
  • «исход неизвестен» — отдельная видимая категория в своде, а не молчание;
  • «наши» имена — ОДИН список на два вопроса (мы разместили / мы выиграли).

Run: python3 -m crawler.tests.test_outcome   (exit 1 on any failure)
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

from crawler.core.outcome import (
    OWN_ORG_FRAGMENTS, classify_deal, funnel, is_our_win, lot_key_from_url,
    merge_result, participants_from_extra, winner_from_extra,
)


# --- ключ сшивки ------------------------------------------------------------

def test_lot_key_from_plain_url():
    assert lot_key_from_url("https://etender.uzex.uz/lot/508273") == "508273"


def test_lot_key_survives_query_and_trailing_parts():
    assert lot_key_from_url("https://etender.uzex.uz/lot/506231?tab=docs") == "506231"
    assert lot_key_from_url("https://etender.uzex.uz/lot/506231/") == "506231"


def test_lot_key_is_none_for_foreign_urls():
    """У Cooperation/XT-Xarid/Telegram ссылки другой формы — авто-сшивки для
    них нет и притворяться, что есть, нельзя."""
    for u in ("https://new.cooperation.uz/purchase/12345",
              "https://xt-xarid.uz/procedure/7749217",
              "https://t.me/prmediagroup/8817", "", None):
        assert lot_key_from_url(u) is None, u


def test_lot_key_ignores_digits_elsewhere_in_the_url():
    assert lot_key_from_url("https://etender.uzex.uz/2026/tender/508273") is None


# --- кто выиграл ------------------------------------------------------------

def test_our_win_recognized_in_both_alphabets():
    assert is_our_win('ЧП "ВИНЧ" (ИНН 123)')
    assert is_our_win("WINCH MCHJ")
    assert is_our_win("Салахутдинов Д.У")


def test_competitor_is_not_our_win():
    """Живые победители наших лотов — все типографии-конкуренты."""
    for w in ('OOO "PREMIUM POLIGRAF BIZNES" (ИНН 303018986)',
              "ЧП PECHATNIK VOSTOKA (ИНН 308044785)",
              "FARG`ONA KITOB OLAMI MCHJ (ИНН 303937334)", None, ""):
        assert not is_our_win(w), w


def test_own_org_list_is_shared_with_the_notifier():
    """Один список на два вопроса. Своя копия в notifier означала бы, что
    выигранный нами лот в отчёте об исходах уходит конкуренту."""
    from crawler.core import notifier
    assert notifier._OWN_ORG_FRAGMENTS is OWN_ORG_FRAGMENTS


# --- чтение фида ------------------------------------------------------------

def test_winner_is_read_from_extra_info_not_from_the_winner_column():
    ei = {"Победитель": 'ООО "X" (ИНН 200935397)', "Участников": "4"}
    assert winner_from_extra(ei) == 'ООО "X" (ИНН 200935397)'


def test_empty_winner_template_is_absence_not_a_name():
    """Шаблон '{provider_name} (ИНН {provider_inn})' на пустых полях даёт
    '(ИНН )' — это отсутствие победителя, а не поставщик с таким именем."""
    for raw in ("", "   ", "(ИНН )", "None (ИНН None)"):
        assert winner_from_extra({"Победитель": raw}) is None, repr(raw)


def test_winner_from_garbage_extra_info():
    assert winner_from_extra(None) is None
    assert winner_from_extra("строка") is None
    assert winner_from_extra({}) is None


def test_participants_missing_is_none_not_zero():
    """Ноль участников — осмысленное утверждение (никто не пришёл). Отсутствие
    поля — нет, и подменять одно другим нельзя."""
    assert participants_from_extra({}) is None
    assert participants_from_extra({"Участников": "не указано"}) is None
    assert participants_from_extra({"Участников": "0"}) == 0
    assert participants_from_extra({"Участников": 4}) == 4


# --- классификация ----------------------------------------------------------

def test_deal_with_competitor_winner():
    out = classify_deal({"Победитель": "ЧП PECHATNIK VOSTOKA (ИНН 308044785)",
                         "Участников": "3"}, price=1200.0)
    assert out["lot_result"] == "won_by_other"
    assert out["participants"] == 3 and out["result_price"] == 1200.0
    assert out["result_source"] == "auto:etender-deals"


def test_deal_won_by_us():
    out = classify_deal({"Победитель": 'ЧП "ВИНЧ" (ИНН 999)'})
    assert out["lot_result"] == "won_by_us"


def test_deal_without_a_disclosed_winner_is_not_a_failed_lot():
    """ГЛАВНОЕ свойство. Сделка есть, победитель скрыт — исход НЕИЗВЕСТЕН.
    Вернуть 'no_deal' значило бы записать несостоявшимися сотни разыгранных
    лотов и отчитаться, что конкуренции нет."""
    assert classify_deal({"Победитель": "(ИНН )"}) is None
    assert classify_deal({}) is None


# --- слияние ----------------------------------------------------------------

_AUTO = {"lot_result": "won_by_other", "winner": "X", "result_source": "auto:etender-deals"}


def test_first_auto_result_is_written():
    assert merge_result(None, _AUTO) == _AUTO


def test_human_answer_is_never_overwritten_by_automation():
    """Кнопка Данияра — истина последней инстанции. Ночной прогон, стирающий
    её молча, хуже отсутствия прогона."""
    human = {"lot_result": "won_by_us", "result_source": "button", "winner": None}
    assert merge_result(human, _AUTO) is None


def test_repeated_identical_sync_writes_nothing():
    assert merge_result(dict(_AUTO), _AUTO) is None


def test_changed_verdict_is_written():
    old = {"lot_result": "no_deal", "result_source": "auto:etender-notdealed", "winner": None}
    assert merge_result(old, _AUTO) == _AUTO


def test_incoming_without_a_result_is_ignored():
    assert merge_result(None, {}) is None
    assert merge_result(None, {"lot_result": None}) is None


# --- свод -------------------------------------------------------------------

def test_funnel_counts_two_axes_independently():
    rows = [
        {"our_action": "bid", "lot_result": "won_by_us"},
        {"our_action": "bid", "lot_result": "won_by_other"},
        {"our_action": "bid", "lot_result": None},
        {"our_action": None, "lot_result": "won_by_other"},
        {"our_action": "passed", "lot_result": "no_deal"},
        {"our_action": None, "lot_result": None},
    ]
    f = funnel(rows)
    assert f["rows"] == 6
    assert f["bid"] == 3 and f["passed"] == 1 and f["action_unknown"] == 2
    assert f["won_by_us"] == 1 and f["won_by_other"] == 2 and f["no_deal"] == 1
    assert f["result_unknown"] == 2
    assert f["bid_and_won"] == 1 and f["bid_result_unknown"] == 1


def test_funnel_on_todays_reality_is_all_unknown():
    """Состояние на 20.08: кликов нет, исходов нет. Свод обязан показывать это
    явно, а не пустыми нулями по всем осям сразу."""
    f = funnel([{"our_action": None, "lot_result": None}] * 5)
    assert f["action_unknown"] == 5 and f["result_unknown"] == 5
    assert f["bid"] == 0 and f["won_by_us"] == 0


def test_funnel_of_nothing_does_not_divide_by_zero():
    f = funnel([])
    assert f["rows"] == 0 and f["result_unknown"] == 0


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
