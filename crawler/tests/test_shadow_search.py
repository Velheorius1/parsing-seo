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

from crawler.scripts.shadow_search import AUDIT_CANDIDATES, _matches


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
