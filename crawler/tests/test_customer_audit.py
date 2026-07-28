"""Guards for customer_audit name matching and INN aggregation (2026-07-27).

The audit lives or dies on the name matcher: the SAME bank appears as
«АКБ „Узпромстройбанк"», «AKSIYADORLIK TIJORAT XALQ BANKI» and «AT XALQ BANKI»
across sources, so a naive substring test finds a third of the rows and the
funnel silently under-reports. Branch INNs differ from head-office INNs, so the
resolver must return a SET.

Run: python3 -m crawler.tests.test_customer_audit   (exit 1 on any failure)
"""
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="печать", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.scripts.customer_audit import _matches, _norm, resolve_inns  # noqa: E402


def _pats(*p):
    return [_norm(x) for x in p]


# ── name matching across the real spellings ──────────────────────────────────

def test_xalq_matches_all_observed_spellings():
    pats = _pats("xalq bank", "халк банк")
    for observed in ("AKSIYADORLIK TIJORAT XALQ BANKI", "AT XALQ BANKI",
                     "AT “Xalq banki”", "Xalq banki Sirdaryo viloyatida"):
        assert _matches(observed, pats), observed


def test_sqb_matches_cyrillic_and_latin():
    pats = _pats("узпромстройбанк", "sanoat qurilish", "sqb")
    for observed in ('АКБ «Узпромстройбанк»', "Узпромстройбанк (SQB)",
                     "SANOAT QURILISH BANK", 'АКБ "УЗПРОМСТРОЙБАНК"'):
        assert _matches(observed, pats), observed


def test_legal_form_noise_does_not_block_a_match():
    # «AT XALQ BANKI» → stripping the legal tokens must still leave "xalq"
    # adjacent enough to match the pattern.
    assert _matches("AT XALQ BANKI", _pats("xalq"))
    assert _matches("АКБ Узпромстройбанк", _pats("узпромстройбанк"))


def test_unrelated_org_does_not_match():
    pats = _pats("xalq bank", "узпромстройбанк")
    for other in ("АО Узбекнефтегаз", "Ipoteka Bank", "Hamkorbank",
                  "УЗСМ (Металлургия)", ""):
        assert not _matches(other, pats), other


def test_norm_is_quote_and_case_insensitive():
    assert _norm('АКБ «Узпромстройбанк»') == _norm("акб узпромстройбанк")
    assert _norm("  Xalq   Banki  ") == "xalq banki"


# ── INN resolution ───────────────────────────────────────────────────────────

def test_branches_contribute_their_own_inns():
    """Head office and a branch under different INNs must BOTH be collected."""
    pats = _pats("xalq bank")
    platform = [
        ("etender", {"customer_inn": "207215726", "customer_name": "AT XALQ BANKI"}),
        ("etender", {"customer_inn": "301112233",
                     "customer_name": "XALQ BANKI Sirdaryo filiali"}),
        ("etender", {"customer_inn": "999999999", "customer_name": "Hamkorbank"}),
    ]
    inns = resolve_inns(platform, [], pats)
    assert set(inns.keys()) == {"207215726", "301112233"}, dict(inns)
    assert inns["207215726"]["hits"] == 1


def test_db_extra_info_inns_are_picked_up_both_key_shapes():
    """prequest stores `customer_inn`; cooperation contracts store the Russian
    label «ИНН заказчика» — the resolver must read both."""
    rows = [
        {"organization": "AT XALQ BANKI", "extra_info": {"customer_inn": "207215726"}},
        {"organization": "AT XALQ BANKI", "extra_info": {"ИНН заказчика": "207215726"}},
        {"organization": "AT XALQ BANKI", "extra_info": {}},
    ]
    inns = resolve_inns([], rows, _pats("xalq"))
    assert list(inns.keys()) == ["207215726"]
    assert inns["207215726"]["hits"] == 2


def test_platform_rows_with_nonmatching_name_are_ignored():
    platform = [("etender", {"customer_inn": "111", "customer_name": "Другая компания"})]
    assert dict(resolve_inns(platform, [], _pats("xalq"))) == {}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, "-", str(e)[:140])
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
