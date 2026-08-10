"""Пины мерила для разборщика лотов (10.08).

До этого дня тракт разбора не мерился ничем: версионный скоркарт проверяет гейт
релевантности, а разбор — отдельная машина с инструментами, и её ошибка стоит
дороже (вердикт по лоту на 1,23 млрд). Единственный раз, когда ошибку поймали, —
Данияр прочитал вердикт глазами.

Меряется ПРОФИЛЬ («наш профиль» / «не наш профиль» / «неясно»), а не вердикт.
Первый прогон 10.08 показал, почему: лот «Nashrlarni chop etish xizmati» — прямая
печать изданий — получил «пропустить», и это было ВЕРНО (лот уже разыгран,
контракт подписан). Вердикт отвечает «заходить ли сейчас», разметка из кликов —
«наш ли это тип работы». Исторический корпус со временем закрывается весь, и
измерение по вердикту меряло бы календарь. Разборщик теперь отдаёт оба ответа.

Свойства, которые тут держатся:
  • «неясно» НЕ штрафуется — промпт сам требует его вместо «не наш профиль» при
    непрочитанном ТЗ; но доля таких ответов выводится отдельно, иначе разборщик,
    отвечающий «неясно» всегда, покажет идеальную точность и нулевую пользу;
  • сбой (ответа нет вовсе) НЕ идёт в знаменатель точности — иначе гниение
    корпуса (умерла страница лота) читалось бы как деградация разборщика;
  • корпус сбалансирован по обеим сторонам: только «не наше» позволило бы
    набрать высокий балл, отвечая «не наш профиль» на всё;
  • у каждой записи есть ссылка на источник разметки — цифра без происхождения
    не цифра.

Run: python3 -m crawler.tests.test_investigator_bench   (exit 1 on any failure)
"""
import json
import os
import sys
import types


def _load():
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(openrouter_api_key="", telegram_bot_token="",
                                           telegram_alert_chat_id="", supabase_url="",
                                           supabase_service_role_key="",
                                           ai_relevance_model="stub")
        sys.modules[cfg] = m
    import crawler.scripts.investigator_bench as B
    return B


B = _load()


def _res(cid, expect, got):
    return {"cid": cid, "expect": expect, "got": got, "title": "t",
            "outcome": B.classify(expect, got)}


# --- исход одной записи -----------------------------------------------------

def test_matching_verdict_is_correct():
    assert B.classify(B.OURS, B.OURS) == "верно"
    assert B.classify(B.THEIRS, B.THEIRS) == "верно"


def test_opposite_verdict_is_a_hard_miss():
    assert B.classify(B.OURS, B.THEIRS) == "грубый промах"
    assert B.classify(B.THEIRS, B.OURS) == "грубый промах"


def test_hedge_is_neither_right_nor_wrong():
    """Промпт прямо требует «уточнить» вместо «пропустить» без ТЗ — штрафовать
    за исполнение собственного правила нельзя."""
    assert B.classify(B.OURS, B.HEDGE) == "уточнение"
    assert B.classify(B.THEIRS, B.HEDGE) == "уточнение"


def test_absent_verdict_is_a_failure_not_a_miss():
    assert B.classify(B.OURS, None) == "сбой"
    assert B.classify(B.OURS, "") == "сбой"


# --- арифметика отчёта ------------------------------------------------------

def test_accuracy_counts_only_decisive_answers():
    rs = [_res("a", B.OURS, B.OURS),
          _res("b", B.THEIRS, B.OURS),
          _res("c", B.OURS, B.HEDGE),
          _res("d", B.THEIRS, None)]
    sc = B.score(rs)
    assert sc["decisive"] == 2 and sc["accuracy"] == 0.5, sc
    assert sc["уточнение"] == 1 and sc["сбой"] == 1


def test_hedging_everything_gives_no_accuracy_not_a_perfect_score():
    """Ключевое свойство: уклонение не конвертируется в отличный балл."""
    sc = B.score([_res("a", B.OURS, B.HEDGE), _res("b", B.THEIRS, B.HEDGE)])
    assert sc["accuracy"] is None, sc
    assert sc["hedge_rate"] == 1.0


def test_failures_do_not_drag_accuracy_down():
    """Умершая страница лота — не ошибка суждения."""
    sc = B.score([_res("a", B.OURS, B.OURS), _res("b", B.THEIRS, None)])
    assert sc["accuracy"] == 1.0 and sc["fail_rate"] == 0.5, sc


def test_misses_are_listed_not_just_counted():
    sc = B.score([_res("x", B.OURS, B.THEIRS)])
    assert [m["cid"] for m in sc["misses"]] == ["x"]


def test_empty_run_does_not_divide_by_zero():
    sc = B.score([])
    assert sc["accuracy"] is None and sc["hedge_rate"] is None


def test_report_always_shows_hedge_and_failure_rates():
    """Точность без этих двух чисел вводит в заблуждение — их нельзя терять."""
    txt = B.format_report(B.score([_res("a", B.OURS, B.OURS)]), "inv-v2")
    assert "неясно" in txt and "сбоев" in txt, txt


def test_report_survives_a_run_with_no_decisive_answers():
    txt = B.format_report(B.score([_res("a", B.OURS, B.HEDGE)]), "inv-v1")
    assert "не определена" in txt, txt


# --- разделение двух вопросов в самом разборщике ------------------------------

def _investigator():
    if "supabase" not in sys.modules:
        sb = types.ModuleType("supabase")
        sb.create_client = lambda *a, **k: None
        sb.Client = object
        sys.modules["supabase"] = sb
    import crawler.scripts.investigator as I
    return I


def test_investigator_returns_profile_as_a_separate_required_field():
    """Без отдельного поля вердикт по закрытому лоту неотличим от «не наша работа»,
    и корпус мерил бы календарь. Поле обязательное: необязательное молча исчезнет."""
    I = _investigator()
    sv = [t for t in I.TOOLS if t["function"]["name"] == "submit_verdict"][0]["function"]
    props = sv["parameters"]["properties"]
    assert "profile" in props, list(props)
    assert props["profile"]["enum"] == [B.OURS, B.THEIRS, B.HEDGE], props["profile"]
    assert "profile" in sv["parameters"]["required"], sv["parameters"]["required"]


def test_prompt_tells_the_model_the_two_questions_are_different():
    I = _investigator()
    s = I.SYSTEM
    assert "НЕ СМЕШИВАЙ" in s or "не смешивай" in s.lower(), "разделение должно быть в промпте"
    assert "разыгран" in s, "нужен явный пример: закрытый лот всё равно нашего профиля"


def test_profile_is_shown_to_daniyar_not_just_measured():
    """«Пропустить по сроку» и «пропустить, потому что чужая работа» — разные
    новости: по первой ещё можно поискать тот же предмет на другой площадке."""
    I = _investigator()
    msg = I._format_verdict_msg(1, {"verdict": "пропустить", "profile": B.OURS, "why": "x"})
    assert B.OURS in msg, msg


# --- корпус -----------------------------------------------------------------

def test_corpus_loads_and_is_not_tiny():
    entries, meta = B.load_corpus()
    assert len(entries) >= 10, len(entries)
    assert meta.get("corpus_version")


def test_corpus_is_balanced_on_both_sides():
    """Перекос в «не наше» позволил бы набрать балл, отвечая «пропустить» на всё."""
    entries, _ = B.load_corpus()
    ours = [e for e in entries if e["expect"] == B.OURS]
    theirs = [e for e in entries if e["expect"] == B.THEIRS]
    assert len(ours) >= 4 and len(theirs) >= 4, (len(ours), len(theirs))


def test_every_entry_is_well_formed():
    entries, _ = B.load_corpus()
    seen = set()
    for e in entries:
        for k in ("cid", "alert_seq", "external_id", "source", "expect",
                  "ground_truth", "why_hard"):
            assert e.get(k), (e.get("cid"), k)
        assert e["expect"] in (B.OURS, B.THEIRS), e["expect"]
        assert e["cid"] not in seen, "cid не уникален: %s" % e["cid"]
        seen.add(e["cid"])


def test_every_label_names_its_source():
    """Разметка без происхождения — это мнение, а не правда."""
    entries, _ = B.load_corpus()
    for e in entries:
        gt = e["ground_truth"].lower()
        assert ("клик" in gt) or ("вручную" in gt), (e["cid"], e["ground_truth"])


def test_corpus_json_is_valid_utf8_json():
    with open(B._CORPUS, encoding="utf-8") as f:
        json.load(f)


def test_the_case_that_started_this_is_in_the_corpus():
    """Лот 506231 — единственная пойманная ошибка тракта; потерять её нельзя."""
    entries, _ = B.load_corpus()
    assert any(e["external_id"] == "26120012506231" for e in entries)


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
