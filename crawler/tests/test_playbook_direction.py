"""Пины направления обучения playbook (10.08).

Случай, из которого это выросло. В classifier_playbook лежали 7 строк recall-таксономии
relevant-rejected («наш заказ ошибочно зарезан»). Четыре кандидата из них оказались
принципами, ПРОТИВОПОЛОЖНЫМИ тому, что сказал человек — сверка с исходными кликами
alert_feedback:

  «Roll up kerak 150x200 cm»       человек: client → принцип: «не является заказом
                                                      на полиграфическую продукцию»
  «Бумага и изделия из бумаги»     человек: client → принцип: «закупка товара для
                                                      собственных нужд, а не наш тендер»
  «Ручка металлическая»            человек: client → принцип: «мы не принимаем заказы
                                                      на закупку, производство, поставку»
  «Нужны крафт пакеты с печатью»   человек: client → принцип: «не является заказом на
                                                      полиграфию, а относится к упаковке»

Почему это опаснее обычной ошибки: get_relevance_playbook закрепляет ВСЕ recall-принципы
в промпте ПЕРВЫМИ и безусловно (rest[:limit-len(recall)] — отказные конкурируют за
остаток слотов, recall не конкурирует ни с кем). Все четыре стояли на support_count=1,
а промоушен кандидата в active случается на support_count>=2 — то есть ОДНА следующая
совпавшая коррекция вывела бы их в промпт первыми строками.

Свойства, которые тут держатся:
  • отказная коррекция не может родить recall-принцип (таксономия сводится с
    направлением, а направление известно достоверно — из метки человека);
  • дистиллятор объявляет, что делает его принцип (widen/narrow), и заявка
    сверяется с направлением — инверсия не доезжает до playbook;
  • сверка fail-open: нет поля — пропускаем. Молчаливая остановка обучения уже
    стоила 358 коррекций (reasoning:false), повторять нельзя;
  • имя recall-таксономии — ОДНА строка на весь репозиторий (feedback.py), потому
    что расхождение копий здесь не падает, а тихо искажает промпт.

Run: python3 -m crawler.tests.test_playbook_direction   (exit 1 on any failure)
"""
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
    if "supabase" not in sys.modules:  # локально пакет не установлен — сети тут и не нужно
        sb = types.ModuleType("supabase")
        sb.create_client = lambda *a, **k: None
        sb.Client = object
        sys.modules["supabase"] = sb
    import crawler.scripts.playbook_refine as P
    import crawler.core.feedback as F
    return P, F


P, F = _load()


# --- таксономия сводится с направлением ------------------------------------

def test_protect_always_lands_in_recall_taxonomy():
    assert P.enforce_direction("irrelevant-niche", "protect", "client") == P.RECALL_TAXONOMY
    assert P.enforce_direction("wrong-score", "protect", "client") == P.RECALL_TAXONOMY


def test_reject_can_never_produce_a_recall_principle():
    """Человек снял лот — принцип обязан сужать, а не расширять."""
    assert P.enforce_direction(P.RECALL_TAXONOMY, "reject", "ad") == "ad-as-client"
    assert P.enforce_direction(P.RECALL_TAXONOMY, "reject", "irrelevant") == "irrelevant-niche"


def test_unknown_human_label_falls_back_to_a_reject_taxonomy():
    got = P.enforce_direction(P.RECALL_TAXONOMY, "reject", "")
    assert got != P.RECALL_TAXONOMY and got in P.TAXONOMY, got


def test_honest_reject_taxonomies_pass_through_untouched():
    for tx in ("ad-as-client", "irrelevant-niche", "wrong-score"):
        assert P.enforce_direction(tx, "reject", "ad") == tx


# --- самопроверка направления принципа --------------------------------------

def test_narrowing_principle_on_a_recall_correction_is_rejected():
    """Ровно случай крафт-пакетов: человек сказал НАШ, принцип сказал «не наш»."""
    assert P.effect_conflicts("narrow", "protect") is True


def test_widening_principle_on_a_rejection_is_rejected():
    assert P.effect_conflicts("widen", "reject") is True


def test_agreeing_effect_passes():
    assert P.effect_conflicts("widen", "protect") is False
    assert P.effect_conflicts("narrow", "reject") is False
    assert P.effect_conflicts("NARROW", "reject") is False, "регистр не должен решать"


def test_missing_effect_is_fail_open():
    """Нет поля — учимся дальше. Молчаливая остановка обучения дороже."""
    for bad in (None, "", "  ", "maybe", "widen-ish"):
        assert P.effect_conflicts(bad, "protect") is False, bad


# --- промпты ----------------------------------------------------------------

def test_reject_prompt_no_longer_offers_the_recall_option():
    """Модель не должна выбирать вариант, невозможный по построению."""
    body = P._DISTIL_PROMPT.split("Таксономия (выбери ОДНУ):", 1)[1].split("\n", 1)[0]
    assert P.RECALL_TAXONOMY not in body, body


def test_protect_prompt_forbids_writing_a_rejection():
    assert "ЗАПРЕЩЕНО" in P._DISTIL_PROMPT_PROTECT
    assert "РАСШИРЯТЬ" in P._DISTIL_PROMPT_PROTECT


def test_both_prompts_ask_the_model_to_declare_its_effect():
    for p in (P._DISTIL_PROMPT, P._DISTIL_PROMPT_PROTECT):
        assert '"effect"' in p, p[:80]


# --- общая константа --------------------------------------------------------

def test_recall_taxonomy_has_a_single_definition():
    """Копия строки в двух модулях разъезжается молча — и промпт перекашивается."""
    assert P.RECALL_TAXONOMY is F.RECALL_TAXONOMY
    assert F.RECALL_TAXONOMY == "relevant-rejected"


def test_recall_taxonomy_stays_valid_for_the_protect_branch():
    assert P.RECALL_TAXONOMY in P.TAXONOMY


def test_pinning_is_unconditional_for_recall():
    """Свойство, из-за которого цена загрязнения несимметрична: отказные принципы
    режутся лимитом, recall — нет. Пин на случай «оптимизации» этой строки."""
    src = open(F.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "rows = recall + rest[:max(0, limit - len(recall))]" in src


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
