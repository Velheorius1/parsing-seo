"""Regression guards for the feedback→playbook learning signal (Hole A fix 2026-07-16).

Pure-logic tests (no DB/network) protecting the correctness of what the classifier
learns from a click. Before the fix, original_label stored message_type (a content
type), so `original != corrected` was ALWAYS true → every click (incl. 92 agreements)
was distilled as an "error", and false-negatives were structurally uncountable.

Now original_label stores the system VERDICT (_system_verdict), and _classify turns a
(verdict, human) pair into: 'reject' (false positive), 'protect' (recall guard), or
'' (agreement → skip). alert_feedback holds only SHOWN items, so a true missed tender
never appears here — that gap is recall_audit's job, not the click loop's.

Run: python3 -m crawler.tests.test_feedback_learning   (exit 1 on any failure)
"""
from crawler.core.feedback import _system_verdict
from crawler.scripts.playbook_refine import _classify


def test_verdict_category_wins():
    # An explicit AI category is authoritative regardless of score.
    assert _system_verdict("client", 90) == "client"
    assert _system_verdict("ad", 80) == "ad"
    assert _system_verdict("irrelevant", 40) == "irrelevant"


def test_verdict_score_band_when_no_category():
    # No category → fall back to the 70 score band (midpoint of observed 90/45).
    assert _system_verdict(None, 90) == "client"
    assert _system_verdict(None, 70) == "client"
    assert _system_verdict(None, 69) == "weak"
    assert _system_verdict("", 55) == "weak"


def test_verdict_defaults_to_alerted():
    # Shown but no verdict at all → 'alerted' (relevant), never 'unknown'.
    assert _system_verdict(None, None) == "alerted"
    assert _system_verdict("", None) == "alerted"


def test_verdict_bad_score_is_safe():
    # A non-numeric score must not crash; falls through to 'alerted'.
    assert _system_verdict(None, "oops") == "alerted"


def test_classify_false_positive():
    # Human says ad/irrelevant on a shown item → false positive, learn to reject.
    assert _classify("client", "irrelevant") == "reject"
    assert _classify("alerted", "ad") == "reject"
    assert _classify("client", "ad") == "reject"


def test_classify_recall_guard():
    # Human says client on something the system under-rated → protect (recall guard).
    assert _classify("weak", "client") == "protect"
    assert _classify("irrelevant", "client") == "protect"
    assert _classify("ad", "client") == "protect"


def test_classify_agreement_skipped():
    # Human confirms a relevant verdict → no signal, skip (saves an LLM call).
    assert _classify("client", "client") == ""
    assert _classify("alerted", "client") == ""


def test_classify_survivors_filter():
    # The fetch_corrections loop keeps only rows with a non-empty direction.
    rows = [
        {"original_label": "client", "corrected_label": "client"},      # agreement -> drop
        {"original_label": "alerted", "corrected_label": "irrelevant"}, # FP -> keep
        {"original_label": "weak", "corrected_label": "client"},        # recall -> keep
        {"original_label": "alerted", "corrected_label": "client"},     # agreement -> drop
    ]
    kept = [r for r in rows if _classify(r["original_label"], r["corrected_label"])]
    assert len(kept) == 2
    dirs = sorted(_classify(r["original_label"], r["corrected_label"]) for r in kept)
    assert dirs == ["protect", "reject"]


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError:
            print("FAIL", fn.__name__)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
