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
    api = {"1": "A (ИНН 111111111)", "2": "B (ИНН 222222222)"}     # ключи — civil_key
    db = [
        {"id": "u1", "external_id": "result-1", "winner": "A"},                  # имя без ИНН → правим
        {"id": "u2", "external_id": "result-2", "winner": "B (ИНН 222222222)"},  # уже верно
        {"id": "u3", "external_id": "result-3", "winner": "C"},                  # API не знает → не трогаем
        {"id": "u4", "external_id": "result-1", "winner": None},                 # пусто → заполняем
    ]
    got = plan(db, api)
    assert [t["id"] for t in got] == ["u1", "u4"], got
    assert got[0]["new"] == "A (ИНН 111111111)"


def test_garbage_inn_is_not_written():
    assert rt.format_winner({"provider_name": "X MCHJ", "provider_inn": "00450"}) == "X MCHJ"
    assert rt.format_winner({"provider_name": "X MCHJ", "provider_inn": "0"}) == "X MCHJ"
    assert rt.format_winner({"provider_name": "", "provider_inn": "000000000"}) is None
    assert rt.format_winner({"provider_name": "X", "provider_inn": "12ab56789"}) == "X"


def test_legacy_colon_format_is_still_read():
    assert winner_inn("ИНН: 204247640") == "204247640"
    assert winner_name("ИНН: 204247640") == "ИНН 204247640"   # не пустая строка в сводке


def test_backfill_does_not_erase_an_existing_inn():
    from crawler.scripts.backfill_civil_winner_inn import plan
    api = {"result-1": "Карши шахри"}                          # API теперь без ИНН
    db = [{"id": "u1", "external_id": "result-1", "winner": "ИНН: 204247640"}]
    assert plan(db, api) == []


def test_civil_key_ignores_the_format_switch_of_24_sep():
    from crawler.scripts.backfill_civil_winner_inn import civil_key
    old, new = "result-26120000010069", "result-26120500010069"
    assert civil_key(old) == civil_key(new) == "261200010069", (civil_key(old), civil_key(new))
    assert civil_key(old) != civil_key("result-26110000010069"), "префикс 2611 — другой раздел"
    assert civil_key("result-12345") == "12345", "нестандартный id остаётся как есть"


def test_backfill_reaches_old_format_rows_through_the_new_id():
    from crawler.scripts.backfill_civil_winner_inn import civil_key, plan
    api = {civil_key("result-26120500010069"): "A (ИНН 111111111)"}      # API теперь отдаёт «05»
    db = [{"id": "old", "external_id": "result-26120000010069", "winner": "ИНН: 111111111"},
          {"id": "new", "external_id": "result-26120500010069", "winner": "A"}]
    got = plan(db, api)
    assert [t["id"] for t in got] == ["old", "new"], got


class _Resp(object):
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def _api_row(n, total):
    return {"display_id": "2612%010d" % n, "civil_name": "Т%d" % n, "total_count": str(total),
            "provider_name": "P%d" % n, "provider_inn": "3%08d" % n, "status_name": "Сделка совершена"}


def test_fetch_api_pages_until_empty_and_reports_completeness():
    from crawler.scripts import backfill_civil_winner_inn as B
    pages = {0: [_api_row(i, 3) for i in range(2)], B.PAGE: [_api_row(2, 3)]}
    calls = []

    def post(url, json=None, headers=None):
        calls.append((json["from"], json["to"]))
        return _Resp(pages.get(json["from"], []))
    api, raw, total = B.fetch_api(10, post=post, pause=0)
    assert (raw, total, len(api)) == (3, 3, 3), (raw, total, len(api))
    assert calls == [(0, B.PAGE - 1), (B.PAGE, 2 * B.PAGE - 1), (2 * B.PAGE, 3 * B.PAGE - 1)], calls
    from crawler.scripts.backfill_civil_winner_inn import civil_key
    assert api[civil_key("result-2612%010d" % 1)] == "P1 (ИНН 300000001)", api


def test_fetch_api_flags_incomplete_when_page_cap_hits_first():
    from crawler.scripts import backfill_civil_winner_inn as B
    api, raw, total = B.fetch_api(1, pause=0, post=lambda url, json=None, headers=None: _Resp(
        [_api_row(i, 9292) for i in range(3)]))
    assert raw == 3 and total == 9292 and raw < total


def test_fetch_api_rejects_non_list_payload():
    from crawler.scripts import backfill_civil_winner_inn as B
    try:
        B.fetch_api(1, pause=0, post=lambda url, json=None, headers=None: _Resp({"error": "x"}))
    except RuntimeError:
        return
    raise AssertionError("не-список должен падать, а не считаться пустым ответом")


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
