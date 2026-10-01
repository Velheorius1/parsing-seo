"""Пины формата победителя в итогах ВМК-69 (01.10.2026).

Из чего выросло. CivilContracts/GetResulted отдаёт и `provider_name`, и
`provider_inn`, но в базу попадало что-то одно: имя (ИНН терялся) либо
«ИНН: 123» (имени нет). Разбор побед конкурентов ищет «ИНН <цифры>», как у
фида сделок etender, — по ВМК-69 он не мог ни сгруппировать фирму, ни
сверить её со списком конкурентов.

Run: python3 -m crawler.tests.test_results_tracker_winner   (exit 1 on any failure)
"""
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="",
        supabase_url="", supabase_service_role_key="",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core import results_tracker as rt  # noqa: E402
from crawler.core.competitor_wins import winner_inn, winner_name  # noqa: E402


def _item(**kw):
    base = {"display_id": "26120500017642", "civil_name": "Пика", "cost": "100",
            "result_cost": "90", "status_name": "Сделка совершена",
            "customer_name": "QARSHI KECH", "deal_date": "2026-10-30T00:00:00"}
    base.update(kw)
    return base


def test_name_and_inn_both_kept():
    row = rt._build_result_row(_item(provider_name="IBROHIM - FIRDAVS МЧЖ", provider_inn="307469620"))
    assert row["winner"] == "IBROHIM - FIRDAVS МЧЖ (ИНН 307469620)", row["winner"]


def test_comparison_code_reads_inn_and_name_back():
    row = rt._build_result_row(_item(provider_name='"MATRIX" MCHJ', provider_inn="203864183"))
    assert winner_inn(row["winner"]) == "203864183"
    assert winner_name(row["winner"]) == '"MATRIX" MCHJ'


def test_inn_only_is_readable_by_the_same_regex():
    row = rt._build_result_row(_item(provider_name=None, provider_inn="204247640"))
    assert row["winner"] == "ИНН 204247640", row["winner"]
    assert winner_inn(row["winner"]) == "204247640"


def test_pinfl_of_fourteen_digits_is_read():
    row = rt._build_result_row(_item(provider_name="", provider_inn="30509863160067"))
    assert winner_inn(row["winner"]) == "30509863160067"


def test_name_only_stays_name():
    row = rt._build_result_row(_item(provider_name="GULISTON1", provider_inn=None))
    assert row["winner"] == "GULISTON1"
    assert winner_inn(row["winner"]) is None


def test_address_is_the_last_resort():
    row = rt._build_result_row(_item(provider_name=None, provider_inn=None, provider_address=" Карши шахри "))
    assert row["winner"] == "Карши шахри"


def test_nothing_known_is_none_not_the_word_none():
    row = rt._build_result_row(_item())
    assert row["winner"] is None
    assert "None" not in row["search_text"], row["search_text"]


def test_whitespace_only_fields_do_not_make_an_inn():
    assert rt.format_winner({"provider_name": "  ", "provider_inn": "   "}) is None


def test_search_text_carries_the_inn_too():
    row = rt._build_result_row(_item(provider_name="X MCHJ", provider_inn="123456789"))
    assert "123456789" in row["search_text"]


def test_backfill_plan_changes_only_what_api_disagrees_with():
    from crawler.scripts.backfill_civil_winner_inn import plan
    api = {"result-1": "A (ИНН 111111111)", "result-2": "B (ИНН 222222222)"}
    db = [
        {"id": "u1", "external_id": "result-1", "winner": "A"},                  # имя без ИНН → правим
        {"id": "u2", "external_id": "result-2", "winner": "B (ИНН 222222222)"},  # уже верно
        {"id": "u3", "external_id": "result-3", "winner": "C"},                  # API не знает → не трогаем
        {"id": "u4", "external_id": "result-1", "winner": None},                 # пусто → заполняем
    ]
    got = plan(db, api)
    assert [t["id"] for t in got] == ["u1", "u4"], got
    assert got[0]["new"] == "A (ИНН 111111111)"


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
