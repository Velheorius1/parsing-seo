"""Топ заказчиков по журналу закупок (customer_rank).

ИЗ ЧЕГО ВЫРОСЛО. Балл — траты на полиграфию и мерч за 12 мес ×2 плюс месяцы
13–24 (Данияр, 07.10.2026). Отказ и отмена — не покупка; дата-опечатка площадки
(«2026-12-31» в итогах ВМК-69) не должна поднимать заказчика в «свежие».
"""
import sys
from datetime import date

from crawler.core import purchase_profile as P
from crawler.scripts import customer_rank as R

TODAY = date(2026, 10, 7)


def _row(inn, amount, day, status="accepted", profile="poly", name="ASAKA BANK", subject="Бланки"):
    return {"buyer_inn": inn, "buyer_name": name, "amount_uzs": amount, "awarded_on": day, "status": status,
            "profile": profile, "subject": subject, "feed": "deals", "winner_name": "PRINT MCHJ"}


def test_last_twelve_months_weigh_double():
    rows = [_row("1", 100, "2026-09-01"), _row("2", 150, "2025-06-01", name="AGROBANK")]
    top = R.rank(rows, TODAY, 10)["entities"]
    assert [e["inn"] for e in top] == ["1", "2"], "100×2 свежих важнее 150 старых"
    assert top[0]["score"] == 200 and top[1]["score"] == 150


def test_not_purchases_are_skipped_and_counted():
    rows = [_row("1", 100, "2026-09-01", status="rejected"), _row("1", 100, "2026-09-01", status="cancelled"),
            _row("", 100, "2026-09-01"), _row("1", 100, "2026-12-31"), _row("1", None, "2026-09-01")]
    result = R.rank(rows, TODAY, 10)
    assert result["entities"] == []
    assert result["skipped"] == {"status:rejected": 1, "status:cancelled": 1, "без ИНН": 1,
                                 "дата в будущем": 1, "не в сумах": 1}


def test_entity_shows_one_off_giant_and_winners():
    rows = [_row("1", 900, "2026-09-01", subject="Газета"), _row("1", 100, "2026-08-01", profile="merch")]
    e = R.rank(rows, TODAY, 10)["entities"][0]
    assert (e["purchases"], e["poly"], e["merch"]) == (2, 1, 1)
    assert round(e["biggest_share"], 2) == 0.9 and e["biggest_subject"] == "Газета"
    assert e["winners"] == [("PRINT MCHJ", 1000.0)] and e["segment"] == "банк"


def test_status_line_says_draft_while_rows_are_not_judged():
    measured = {"prompt": P.PROMPT_VERSION, "precision": 0.98, "recall": 0.96, "items": 140}
    full = [{"feed": "deals", "total": 10, "no_inn": 0, "unlabeled": 0}]
    partial = [{"feed": "deals", "total": 10, "no_inn": 0, "unlabeled": 0},
               {"feed": "ebirja_shop", "total": 10, "no_inn": 7, "unlabeled": 9}]
    assert R.status_line(full, measured) == \
        "судья профиля: точность 0,98, полнота 0,96 на отложенной выборке 140 предметов"
    assert "черновик: 9 договоров ещё без оценки, 7 без ИНН" in R.status_line(partial, measured)
    assert "не перемерен" in R.status_line(full, dict(measured, prompt="p3")), "метрики старого промпта не выдаём"


def test_golden_metrics_belong_to_current_prompt():
    """Сменил промпт — перемерь на отложенной выборке и обнови metrics эталона."""
    assert R.judge_metrics().get("prompt") == P.PROMPT_VERSION


def test_html_escapes_customer_names():
    rows = [_row("1", 100, "2026-09-01", name='<b>"TEST" MCHJ</b>')]
    page = R.render_html(R.rank(rows, TODAY, 10), [], TODAY, metrics={})
    assert '<b>"TEST"' not in page and "&lt;b&gt;" in page


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            print("FAIL", test.__name__, "-", repr(exc)[:200])
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)


def test_three_stray_rows_do_not_make_the_list_a_draft():
    """08.10: после починки филиалов «черновиком» список держали 2 строки без оценки
    и 1 без ИНН из 163 тыс. Черновик — когда дыра больше 0,1% журнала."""
    measured = {"prompt": P.PROMPT_VERSION, "precision": 0.98, "recall": 0.96, "items": 140}
    big = [{"feed": "deals", "total": 163000, "no_inn": 1, "unlabeled": 2}]
    assert "черновик" not in R.status_line(big, measured)
    assert "черновик" in R.status_line([dict(big[0], no_inn=400)], measured)


def test_display_name_comes_from_official_feeds_not_an_ebirja_branch():
    """С 08.10 договоры филиала ebirja идут на ИНН головной; имя филиала
    («01140 - Агробанк … бошқармаси») не должно становиться именем заказчика."""
    branch = dict(_row("207243390", 50e6, "2026-09-01", name="01140 - \"Агробанк\" АТБ"), feed="ebirja_shop")
    head = _row("207243390", 40e6, "2026-09-01", name="\"AGROBANK\" ATB")
    result = R.rank([branch, dict(branch), head], date(2026, 10, 8), 10)
    assert result["entities"][0]["name"] == "\"AGROBANK\" ATB"
    only_branch = R.rank([branch], date(2026, 10, 8), 10)
    assert only_branch["entities"][0]["name"] == "01140 - \"Агробанк\" АТБ", "других имён нет — берём что есть"
