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


def test_merch_and_ad_materials_without_print_words_reach_ai():
    """Сверка 07.10: эти предметы без корней «печать/сувенир» уходили в «нет корня»."""
    for subject in ("Merchendayzing mahsulotlarini ishlab chiqarish va yetkazib berish",
                    "Ijodkorlikni rivojlantirish va reklama-axborot materiallarini ishlab chiqarish",
                    "приобретение услуг по изготовлению рекламно-оформительской продукции",
                    "Turizm salohiyatiga bag‘ishlangan tarqatma materiallar"):
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
    assert P.segment("O`ZBEKISTON RESPUBLIKASI MUDOFA VAZIRLIGI HUZURIDAGI") == "силовые", "опечатка площадки"
    assert P.segment("ГУБДД МВД РУз") == "силовые"
    assert P.segment("QORAQALPOGISTON RES.FVB") == "силовые"
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
        if cols.startswith("feed,business_id,subject_hash"):
            self.mode = "judged"        # apply_human: уже размеченных строк нет
        else:
            self.mode = "known" if cols.startswith("subject_hash,profile") else "rows"
        return self

    def __getattr__(self, attr):
        if attr in ("is_", "gt", "order", "limit", "in_", "eq", "neq"):
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
        elif self.mode == "judged":
            r.data = []
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


def test_lots_differing_only_by_number_get_one_verdict():
    """«Prezident sovgʻasi» 1-Lot / 8-Lot: AI 07.10 решил по-разному, 126 млрд ушли в «наше»."""
    rows = [{"id": i, "feed": "deals", "business_id": str(i), "subject_hash": "h%d" % i,
             "subject": "Prezident sovgʻasi to‘plamini xarid qilish %d-Lot" % i} for i in (1, 2, 8)]
    calls = []

    def call(prompt):
        calls.append(prompt)
        return '{"items": [{"i": 1, "p": "none"}]}'
    client = _Client(rows)
    C.run(client, ai_calls=5, dry_run=False, call=call)
    assert len(calls) == 1 and calls[0].count("to‘plamini xarid qilish") == 1, "одна строка на семью"
    assert {u["profile"] for u in client.store["upserts"]} == {"none"} and len(client.store["upserts"]) == 3


def test_human_label_beats_rule_and_ai():
    rows = [{"id": 1, "feed": "deals", "business_id": "1", "subject": "Kitob chop etish", "subject_hash": "h1"},
            {"id": 2, "feed": "deals", "business_id": "2", "subject": "Ko'prik qurilishi", "subject_hash": "h2"}]
    client = _Client(rows)
    C.run(client, ai_calls=5, dry_run=False, call=lambda p: (_ for _ in ()).throw(AssertionError("AI")),
          human={"h:h1": "none", "h:h2": "poly"})
    got = {u["business_id"]: (u["profile"], u["profile_src"]) for u in client.store["upserts"]}
    assert got == {"1": ("none", "human"), "2": ("poly", "human")}


def test_golden_file_labels_whole_family(tmp_path=None):
    import json as _json, tempfile as _tf, os as _os
    path = _os.path.join(_tf.mkdtemp(), "g.json")
    _json.dump({"labels": [{"subject_hash": "x1", "subject": "Prezident sovgʻasi 1-Lot", "label": "none"}]},
               open(path, "w"))
    human = C.human_labels(path)
    rows = [{"id": 1, "feed": "deals", "business_id": "1", "subject": "Prezident sovgʻasi 7-Lot", "subject_hash": "x7"}]
    decided, candidates, pending = C.plan(rows, human)
    assert decided[0]["profile"] == "none" and decided[0]["profile_src"] == "human" and not candidates


def test_real_golden_file_overrides_known_ai_mistakes():
    """«отчопар» — топоним (ВМК-69, 2 млрд); AI 07.10 прочёл в нём «чоп» и записал в печать."""
    human = C.human_labels()
    rows = [{"id": 1, "feed": "civil", "business_id": "1", "subject": "отчопар", "subject_hash": "4084ad45ea58bcbf"},
            {"id": 2, "feed": "deals", "business_id": "2", "subject": "Imkoniyati cheklangan bolalar uchun oʻquv qurollari – “Prezident sovgʻasi” to‘plamini xarid qilish 9-Lot",
             "subject_hash": "zz"}]
    decided, candidates, pending = C.plan(rows, human)
    assert [(r["profile"], r["profile_src"]) for r in decided] == [("none", "human")] * 2 and not candidates


def test_late_human_label_overrides_an_already_judged_row():
    """Метка, добавленная после разметки, переписывает «не наше» правила (Xalq Bank, 07.10)."""
    upserts = []

    class Q(object):
        def __init__(self):
            self.cols = None

        def select(self, cols):
            self.cols = cols
            return self

        def in_(self, *a):
            return self

        def neq(self, col, value):
            assert (col, value) == ("profile_src", "human"), "ручную метку не трогаем"
            return self

        def upsert(self, batch, on_conflict=None):
            upserts.extend(batch)
            return self

        def execute(self):
            class R(object):
                data = [{"feed": "deals", "business_id": "174537", "subject_hash": "4b0005a2bb77441e",
                         "profile": "none", "profile_src": "no_stem"}]
            return R()

    class Client(object):
        def table(self, name):
            return Q()
    n = C.apply_human(Client(), {"h:4b0005a2bb77441e": "poly", "f:x": "none"}, False, "2026-10-07")
    assert n == 1 and upserts[0]["profile"] == "poly" and upserts[0]["profile_src"] == "human"


def test_real_golden_marks_our_cardholder_win_as_poly():
    assert C.human_labels().get("h:4b0005a2bb77441e") == "poly"


def test_ai_budget_leaves_the_rest_unlabeled_for_next_run():
    # Предметы различаются словами, а не цифрами: цифры family_key сливает в одну семью.
    words = ["%s%s" % (a, b) for a in "абвгдежзик" for b in "лмнопр"]
    rows = [{"id": i, "feed": "deals", "business_id": str(i), "subject": "Kitob %s chop etish" % words[i - 1],
             "subject_hash": "h%d" % i} for i in range(1, 61)]
    client = _Client(rows)
    answer = '{"items": [%s]}' % ", ".join('{"i": %d, "p": "poly"}' % i for i in range(1, 26))
    result = C.run(client, ai_calls=1, dry_run=False, call=lambda p: answer)
    assert result["ai_decided_hashes"] == 25 and result["ai_left_hashes"] == 35
    assert len(client.store["upserts"]) == 25, "неоценённые не пишутся «none» — ждут следующего прогона"


def test_nightly_run_shouts_when_ai_or_db_fails():
    """Крон 03:05 пишет в лог, который никто не читает: сбой обязан прийти сообщением."""
    sent = []
    saved = (C.run, C._client, C.alert, sys.argv)
    try:
        C._client = lambda: None
        C.alert = sent.append
        C.run = lambda *a, **k: {"ai_failed_calls": 3, "ai_left_hashes": 40}
        sys.argv = ["purchase_classify", "--alert", "--dry-run"]
        assert C.main() == 1 and len(sent) == 1 and "40 семей" in sent[0]

        sys.argv = ["purchase_classify", "--dry-run"]
        assert C.main() == 1 and len(sent) == 1, "без --alert молчит (ручной прогон)"

        def boom(*a, **k):
            raise RuntimeError("PostgREST 502")
        C.run = boom
        sys.argv = ["purchase_classify", "--alert", "--dry-run"]
        try:
            C.main()
            raise AssertionError("исключение проглочено")
        except RuntimeError:
            pass
        assert len(sent) == 2 and "502" in sent[1]
    finally:
        C.run, C._client, C.alert, sys.argv = saved


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
