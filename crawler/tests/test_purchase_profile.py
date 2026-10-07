"""Судья предмета закупки (purchase_profile) и разметка журнала (purchase_classify).

ИЗ ЧЕГО ВЫРОСЛО. Профиль заказчиков — только полиграфия и мерч с нанесением
(Данияр, 07.10.2026). Живые предметы журнала ломают словарь: «Kitob tumani» —
район, «chop etish qurilmalari» — принтеры, «kundalik tozalash» — уборка. Поэтому
правила отбирают кандидатов, решает AI, а без корня — «не наше».
"""
import sys

from crawler.core import purchase_profile as P
from crawler.scripts import purchase_classify as C


def test_poly_code_decides_without_ai():
    row = {"feed": "ebirja_shop", "subject": "17.23.13.110-00002-Jurnal; Журналы регистрации",
           "subject_codes": ["17.23.13.110-00002"]}
    assert P.rule_verdict(row) == ("poly", "code")


def test_office_paper_code_is_not_poly_by_code():
    row = {"feed": "ebirja_shop", "subject": "Бумага офисная А4", "subject_codes": ["17.23.14.110-00001"]}
    assert P.rule_verdict(row)[1] != "code"


def test_merch_code_goes_to_ai_even_without_print_words():
    """Ручка без логотипа — канцелярия, с логотипом — мерч: решать по тексту."""
    row = {"feed": "ebirja_shop", "subject": "Ручка шариковая", "subject_codes": ["32.99.12.120-00001"]}
    assert P.rule_verdict(row) == (None, "ai")


def test_tricky_words_reach_ai_instead_of_rule_yes():
    for subject in ("Kitob tumani, Tupchoq MFY binosini baholash xizmati",
                    "Chop etish qurilmalari va kartrijlarni texnik xizmat ko‘rsatish",
                    "Maънавий ҳаёт журналининг 3-сонини чоп этиш",
                    "Kompaniya xodimlarini taqdirlash uchun sovg'alar to'plami"):
        assert P.rule_verdict({"feed": "deals", "subject": subject}) == (None, "ai"), subject


def test_no_print_stem_is_none_without_ai():
    assert P.rule_verdict({"feed": "deals", "subject": "Avtomobil yo‘lini rekonstruksiya qilish"}) == \
        ("none", "no_stem")


def test_direct_purchase_waits_for_items_only_in_print_divisions():
    assert P.rule_verdict({"feed": "direct", "category": "Услуги издательские"}) == (None, "pending")
    assert P.rule_verdict({"feed": "direct", "category": "Кокс и нефтепродукты"}) == ("none", "category")
    assert P.rule_verdict({"feed": "ebirja_shop"}) == (None, "pending"), "карточки ещё нет"


def test_prompt_numbers_items_and_answer_maps_back():
    items = [{"subject": "Бланки строгой отчётности", "buyer_name": "XALQ BANK"},
             {"subject": "Ручка", "subject_codes": ["32.99.12"]}]
    prompt = P.build_prompt(items)
    assert "1. Бланки строгой отчётности | XALQ BANK" in prompt and "2. Ручка | коды: 32.99.12" in prompt
    answer = '{"items": [{"i": 1, "p": "poly", "r": "бланки"}, {"i": 2, "p": "none", "r": "без логотипа"}]}'
    assert P.classify_batch(items, lambda prompt: answer) == {0: ("poly", "бланки"), 1: ("none", "без логотипа")}


def test_bad_ai_items_are_dropped_not_guessed():
    answer = '{"items": [{"i": 1, "p": "yes"}, {"i": 9, "p": "poly"}, {"i": "x", "p": "poly"}, "junk"]}'
    assert P.parse_answer(answer, 2) == {}


def test_segments_from_names():
    assert P.segment('"ASAKA" AJ tijorat banki') == "банк"
    assert P.segment("Kapital sug'urta AJ") == "страховая"
    assert P.segment("75061-SONLI XARBIY QISM") == "силовые"
    assert P.segment("Namangan viloyat hokimligi") == "хокимият"
    assert P.segment("Samarqand viloyat xokimligi") == "хокимият"
    assert P.segment("Oliy ta'lim, fan va innovatsiyalar vazirligi") == "министерство/агентство"
    assert P.segment("Yangi O'zbekiston universiteti") == "образование"
    assert P.segment("\"O'ZBEKNEFTGAZ\" AJ") == "госкомпания/АО"
    assert P.segment("PRIME ADVERTISING MCHJ") == "частная компания"
    assert P.segment("Radiopreparat", "Budget buyurtmachi") == "бюджетная организация"


class _Q:
    """Мини-PostgREST: фильтры игнорирует, отдаёт заданные строки один раз."""

    def __init__(self, store, name):
        self.store, self.name, self.mode = store, name, None

    def select(self, cols):
        self.mode = "known" if cols.startswith("subject_hash,profile") else "rows"
        return self

    def __getattr__(self, attr):
        if attr in ("is_", "gt", "order", "limit", "in_", "eq"):
            return lambda *a, **k: self
        if attr == "not_":
            return self
        raise AttributeError(attr)

    def upsert(self, batch, on_conflict=None):
        self.store["upserts"].extend(batch)
        return self

    def execute(self):
        class R:
            pass
        r = R()
        if self.mode == "rows":
            r.data, self.store["rows"] = self.store["rows"], []
        elif self.mode == "known":
            r.data = self.store["known"]
        else:
            r.data = None
        return r


class _Client:
    def __init__(self, rows, known=()):
        self.store = {"rows": rows, "known": list(known), "upserts": []}

    def table(self, name):
        return _Q(self.store, name)


def test_same_subject_is_judged_once_and_applied_to_every_row():
    rows = [{"id": i, "feed": "deals", "business_id": str(i), "subject": "Kitob chop etish", "subject_hash": "h1"}
            for i in range(1, 4)]
    rows.append({"id": 9, "feed": "deals", "business_id": "9", "subject": "Ko'prik qurilishi", "subject_hash": "h2"})
    calls = []

    def call(prompt):
        calls.append(prompt)
        return '{"items": [{"i": 1, "p": "poly", "r": "печать книги"}]}'
    client = _Client(rows)
    result = C.run(client, ai_calls=5, dry_run=False, call=call)
    assert len(calls) == 1 and calls[0].count("Kitob chop etish") == 1
    profiles = {u["business_id"]: (u["profile"], u["profile_src"]) for u in client.store["upserts"]}
    assert profiles == {"1": ("poly", "ai"), "2": ("poly", "ai"), "3": ("poly", "ai"), "9": ("none", "no_stem")}
    assert result["ai_decided_hashes"] == 1


def test_known_verdict_is_reused_without_calling_ai():
    rows = [{"id": 1, "feed": "deals", "business_id": "1", "subject": "Kitob chop etish", "subject_hash": "h1"}]
    client = _Client(rows, known=[{"subject_hash": "h1", "profile": "none", "profile_src": "human"}])
    result = C.run(client, ai_calls=5, dry_run=False, call=lambda p: (_ for _ in ()).throw(AssertionError("AI")))
    assert client.store["upserts"][0]["profile"] == "none" and result["ai_reused"] == 1


def test_ai_budget_leaves_the_rest_unlabeled_for_next_run():
    rows = [{"id": i, "feed": "deals", "business_id": str(i), "subject": "Kitob %d chop etish" % i,
             "subject_hash": "h%d" % i} for i in range(1, 61)]
    client = _Client(rows)
    answer = '{"items": [%s]}' % ", ".join('{"i": %d, "p": "poly"}' % i for i in range(1, 26))
    result = C.run(client, ai_calls=1, dry_run=False, call=lambda p: answer)
    assert result["ai_decided_hashes"] == 25 and result["ai_left_hashes"] == 35
    assert len(client.store["upserts"]) == 25, "неоценённые не пишутся «none» — ждут следующего прогона"


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
