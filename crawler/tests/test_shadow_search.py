"""Fast, network-free safety pins for competitor-audit shadow candidates."""
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

from crawler.scripts.shadow_search import (
    AUDIT_CANDIDATES, _matches, _passes_price_gate, _promotion_block_reason,
    _strict_competitor_price_state, _to_tender,
)


def _candidate(cid):
    return next(c for c in AUDIT_CANDIDATES if c["id"] == cid)


def test_yoriqnoma_variants_match_without_context():
    assert _matches(_candidate("audit-yoriqnoma"), {"title": "Йўриқнома", "search_text": ""})
    assert _matches(_candidate("audit-yoriqnoma"), {"title": "yo'riqnoma", "search_text": ""})


def test_blank_requires_a_print_context():
    cand = _candidate("audit-blank-context")
    assert not _matches(cand, {"title": "blankalar xaridi", "search_text": ""})
    assert _matches(cand, {"title": "blankalar", "search_text": "офсет печать, тираж 500"})


def test_matbaa_requires_a_print_context():
    cand = _candidate("audit-matbaa-context")
    assert not _matches(cand, {"title": "Matbaa korxonasi", "search_text": "xizmat"})
    assert _matches(cand, {"title": "Matbaa", "search_text": "картон пакет, печать"})


def test_jurnal_and_gazeta_variants_match():
    assert _matches(_candidate("audit-jurnal"), {"title": "Рўйхатга олиш журнали", "search_text": ""})
    assert _matches(_candidate("audit-gazeta"), {"title": "davriy nashr", "search_text": ""})


def test_price_gate_matches_production_fail_open_semantics():
    assert not _passes_price_gate({"price": 19_999_999}, 20_000_000)
    assert _passes_price_gate({"price": 20_000_000}, 20_000_000)
    assert _passes_price_gate({"price": None}, 20_000_000)
    assert _passes_price_gate({"price": "unknown"}, 20_000_000)


def test_strict_competitor_price_is_currency_aware_and_strictly_above_20m():
    assert _strict_competitor_price_state({"price": 19_999_999, "currency": "UZS"}) == "rejected"
    assert _strict_competitor_price_state({"price": 20_000_000, "currency": "UZS"}) == "rejected"
    assert _strict_competitor_price_state({"price": 20_000_001, "currency": "UZS"}) == "matched"
    assert _strict_competitor_price_state({"price": None, "currency": "UZS"}) == "unknown"
    assert _strict_competitor_price_state({"price": 50_000_000, "currency": None}) == "unknown"
    assert _strict_competitor_price_state({"price": 50_000_000, "currency": "USD"}) == "unknown"


def test_shadow_tender_and_export_preserve_observed_currency():
    tender = _to_tender({
        "external_id": "1", "title": "Журнал", "organization": "Заказчик",
        "source": "X", "search_text": "Журнал", "price": 25_000_000,
        "currency": "USD",
    })
    assert tender.currency == "USD"


def test_contextual_shadow_candidate_cannot_be_promoted_as_plain_keyword():
    assert _promotion_block_reason(_candidate("audit-blank-context"))
    assert _promotion_block_reason(_candidate("audit-yoriqnoma")) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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


def _module_sources(name):
    # Разбор исходника, а не импорт: recall_audit тянет настоящие settings.
    import ast
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", name)
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SOURCES":
            return set(ast.literal_eval(node.value))
    raise AssertionError("SOURCES не найден в %s — тест ослеп" % name)


def test_shadow_scan_sees_every_etender_feed_the_recall_audit_trusts():
    # 24.09: ВМК-69 подключили в recall_audit, а shadow-поиск новых слов его
    # не видел — пропуски этого источника не находились бы никогда.
    recall = {s for s in _module_sources("recall_audit.py") if s.startswith("ETender")}
    shadow = _module_sources("shadow_search.py")
    assert "ETender Отбор (ВМК-69)" in recall
    assert recall <= shadow, sorted(recall - shadow)
