"""Монитор площадок судит предмет договора (фаза 2b топ-100, 07.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. Сводка 06.10 прислала три победы списка; третья — AKZ SOFIA,
«Topografik-geodezik izlanishlar» для картографического центра Минобороны за
32 млн: конкурент, но не наш профиль. Судья — тот же, что у журнала закупок
(purchase_profile). Чужое не шлём, но считаем; непроверенное шлём с пометкой.
"""
import sys

from crawler.core import competitor_wins as CW
from crawler.scripts import monitor_competitor_awards as monitor
from crawler.scripts import run_all_exchange_competitor_monitor as runner

# Реальные договоры из квитанции монитора 06.10.2026 (публичные карточки ebirja).
AKZ = {"winner_inn": "306911460", "winner_name": "AKZ SOFIA MCHJ", "contract_number": "SH-1",
       "procedure_id": "36452", "amount": 31982500, "currency": "UZS",
       "product_title": "71.12.34.120-00007 Topografik-geodezik izlanishlar bo‘yicha xizmat",
       "classifier_code": "71.12.34.120-00007",
       "classifier_title": "Услуга по топографо-геодезическим изысканиям",
       "description": " | Talab etiladigan ishlar | \n| -- |",
       "buyer_name": "O`ZBEKISTON RESPUBLIKASI MUDOFAA VAZIRLIGI KARTOGRAFIYA MARKAZI"}
EDU = {"winner_inn": "307122222", "winner_name": "EDU PRESS MCHJ", "contract_number": "SL-1",
       "procedure_id": "901", "amount": 631848000, "currency": "UZS", "title": "Книги печатные",
       "buyer_name": "`YANGI O`ZBEKISTON`` UNIVERSITETI DM"}
DIRECT_NO_CARD = {"winner_inn": "305000001", "winner_name": "PRINT MCHJ", "award_id": "D-1",
                  "procedure_id": "77", "final_total": "45000000", "currency": "UZS", "is_win": True,
                  "title": "Услуги издательские", "_specification_status": "unavailable"}


def _runs():
    return {"ebirja_shop": {"status": "complete", "awards": [dict(AKZ)]},
            "ebirja_selection": {"status": "complete", "awards": [dict(EDU)]},
            "uzex_direct": {"status": "complete", "awards": [dict(DIRECT_NO_CARD)]}}


def test_off_profile_win_is_decided_by_rule_without_ai():
    runs, prompts = _runs(), []

    def call(prompt):
        prompts.append(prompt)
        return '{"items": [{"i": 1, "p": "poly", "r": "печать книг"}]}'
    runner.judge_subjects(runs, call)
    akz = runs["ebirja_shop"]["awards"][0]
    assert (akz["profile"], akz["profile_src"]) == ("none", "no_stem")
    assert len(prompts) == 1 and "Topografik" not in prompts[0] and "Книги печатные" in prompts[0]
    edu = runs["ebirja_selection"]["awards"][0]
    assert (edu["profile"], edu["profile_src"]) == ("poly", "ai")


def test_direct_contract_without_card_is_unverified_not_dropped():
    runs = _runs()
    runner.judge_subjects(runs, lambda prompt: '{"items": []}')
    award = runs["uzex_direct"]["awards"][0]
    assert award["profile"] is None and award["profile_src"] == "pending"


def test_ai_failure_leaves_subject_unverified():
    runs = _runs()

    def boom(prompt):
        raise RuntimeError("OpenRouter 502")
    runner.judge_subjects(runs, boom)
    edu = runs["ebirja_selection"]["awards"][0]
    assert edu["profile"] is None and edu["profile_src"] == "ai_error"


def test_judge_crash_marks_every_award_unverified():
    runs = _runs()
    original = runner.judge_subjects
    try:
        runner.judge_subjects = lambda *a, **k: (_ for _ in ()).throw(ValueError("bug"))
        counts = runner._judge_or_mark(runs)
    finally:
        runner.judge_subjects = original
    assert counts.get("judge_error") == 1
    assert {a["profile_src"] for r in runs.values() for a in r["awards"]} == {"judge_error"}


def test_off_profile_award_is_counted_but_not_sent():
    runs = _runs()
    runner.judge_subjects(runs, lambda prompt: '{"items": [{"i": 1, "p": "poly"}]}')
    prior = {"sources": {"ebirja_shop": {"award_keys": []}}}   # не первый запуск
    report = monitor.multi_source_delta(runs, prior)
    sent = [row["winner_name"] for row in report["new_awards"]]
    assert "AKZ SOFIA MCHJ" not in sent and "EDU PRESS MCHJ" in sent and "PRINT MCHJ" in sent
    shop = next(s for s in report["sources"] if s["source_id"] == "ebirja_shop")
    assert shop["off_profile_awards"] == 1 and shop["new_awards"] == 0
    assert [row["winner_name"] for row in report["off_profile_awards"]] == ["AKZ SOFIA MCHJ"]
    assert report["state_candidate"]["sources"]["ebirja_shop"]["award_keys"] == []


def test_unverified_award_carries_a_marker_and_legacy_rows_do_not():
    runs = _runs()
    runner.judge_subjects(runs, lambda prompt: '{"items": [{"i": 1, "p": "poly"}]}')
    report = monitor.multi_source_delta(runs, {"sources": {"x": {}}})
    text = monitor.build_digest(report)
    direct_block = next(b for b in text.split("\n\n") if "PRINT MCHJ" in b)
    edu_block = next(b for b in text.split("\n\n") if "EDU PRESS" in b)
    assert "Предмет не проверен" in direct_block and "Предмет не проверен" not in edu_block
    legacy = monitor.build_digest({"new_awards": [{"winner_name": "OLD", "amount": 1, "title": "x"}]})
    assert "не проверен" not in legacy, "строки outbox до 07.10 профиля не знают — без пометки"


PREMIUM = {"winner_inn": "308000003", "winner_name": "PREMIUM POLIGRAF BIZNES MCHJ", "contract_number": "AU-1",
           "procedure_id": "555", "amount": 343867216, "currency": "UZS",
           "title": "Книга кассира, Банковская резинка, Сургуч, Нить шпагат, Мешок инкассаторский, Пломба",
           "buyer_name": "DAVLAT AKSIYADORLIK TIJORAT BANKI ASAKA AJ"}


def test_ai_none_on_mixed_lot_is_still_sent_with_a_marker():
    """Живой прогон 07.10: AI назвал банковский лот PREMIUM POLIGRAF (344 млн) чужим,
    а 05.10 мы считали эту победу пропуском. Чужим без события — только по правилу."""
    runs = {"ebirja_auction": {"status": "complete", "awards": [dict(PREMIUM)]},
            "ebirja_shop": {"status": "complete", "awards": [dict(AKZ)]}}
    runner.judge_subjects(runs, lambda prompt: '{"items": [{"i": 1, "p": "none", "r": "расходники банка"}]}')
    report = monitor.multi_source_delta(runs, {"sources": {"x": {}}})
    assert [r["winner_name"] for r in report["new_awards"]] == ["PREMIUM POLIGRAF BIZNES MCHJ"]
    assert [r["winner_name"] for r in report["off_profile_awards"]] == ["AKZ SOFIA MCHJ"]
    assert "AI: предмет, похоже, не наш" in monitor.build_digest(report)


def test_human_label_none_is_dropped_like_a_rule():
    row = dict(EDU, profile="none", profile_src="human")
    assert monitor.is_off_profile(row) and not monitor.is_off_profile(dict(EDU, profile="none", profile_src="ai"))


def test_profile_does_not_change_the_award_fingerprint():
    """Пересуд предмета не должен рождать «изменение договора» в сводке."""
    plain = monitor._qualified_source_awards("ebirja_selection", {"awards": [dict(EDU)]})[0]
    judged = monitor._qualified_source_awards("ebirja_selection",
                                              {"awards": [dict(EDU, profile="poly", profile_src="ai")]})[0]
    assert plain["content_hash"] == judged["content_hash"]


def test_digest_line_shows_off_profile_count():
    delta = {"new_awards": [{"key": "k"}], "changed_awards": [], "bootstrap": False, "telegram_delivered": True,
             "sources": [{"source_id": "ebirja_shop", "status": "complete", "off_profile_awards": 1}]}
    line = CW._monitor_line(CW.summarize_monitor(delta, monitor._REPORTED_STATUSES))
    assert "новых договоров 1" in line and "не наш профиль — не слали: 1" in line


def test_build_runs_judges_only_when_asked():
    from datetime import date
    from unittest.mock import patch
    calls = []
    with patch.object(runner, "collect_uzex", lambda *a, **k: {"complete": True, "captured_at": "t", "awards": [],
                                                              "completion": "ok"}), \
            patch.object(runner, "enrich_awards", lambda sid, awards, get, max_details=25: (awards, {})), \
            patch.object(runner, "_ebirja_run", lambda *a, **kw: {"status": "complete", "captured_at": "t",
                                                                  "awards": [dict(AKZ)]}), \
            patch.object(runner, "load_registry", lambda: {}), \
            patch.object(runner, "collect_cooperation", lambda *a, **k: {"complete": True, "completion": "ok"}), \
            patch.object(runner, "_public_rpc_runs", lambda post=None: {}), \
            patch.object(runner, "_judge_or_mark", lambda runs, call=None: calls.append(1) or {"x": 1}):
        runner.build_runs(date(2026, 9, 1), 100, 1, 1)
        assert calls == []
        runs = runner.build_runs(date(2026, 9, 1), 100, 1, 1, judge=True)
        assert calls == [1] and runs["ebirja_shop"]["subject_check"] == {"x": 1}


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
