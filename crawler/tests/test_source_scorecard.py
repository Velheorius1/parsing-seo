"""Regression guards for the source scorecard verdict logic (C, 2026-07-16).

Pure-logic tests (no DB) protecting the demote/promote classification so a noisy source
is flagged and a productive one is protected — the "budget by activity" proposal that
Daniyar acts on. Thresholds live in source_scorecard.

Run: python3 -m crawler.tests.test_source_scorecard   (exit 1 on any failure)
"""
from crawler.scripts.source_scorecard import _verdict


def _r(alerts=0, leads=0, fb=0, miss=0, hit=0):
    return {"alerts": alerts, "leads": leads, "fb": fb, "miss": miss, "hit": hit}


def test_pure_noise_is_demote():
    # 100% miss, no hits, enough clicks → demote (TG: Мин сельхоз shape).
    assert _verdict(_r(alerts=25, fb=19, miss=19, hit=0)) == "demote"


def test_one_hit_saves_from_demote():
    # 94% miss but a single client hit → not demote (ETender UZEX shape).
    assert _verdict(_r(alerts=21, fb=16, miss=15, hit=1)) == "ok"


def test_hits_make_productive():
    # >=2 client hits → productive even amid noise (PR Media shape).
    assert _verdict(_r(alerts=218, fb=81, miss=69, hit=12)) == "productive"


def test_high_volume_no_feedback_is_unrated():
    # Lots of alerts, zero clicks → can't judge → unrated (Cooperation.uz Лоты shape).
    assert _verdict(_r(alerts=82, fb=0)) == "unrated"


def test_too_few_clicks_not_demoted():
    # 2 miss / 0 hit is 100% but below the min-feedback floor → stays ok (not enough signal).
    assert _verdict(_r(alerts=5, fb=2, miss=2, hit=0)) == "ok"


def test_low_volume_unrated_threshold():
    # <10 alerts with no feedback is not "unrated" (too small to bother proposing).
    assert _verdict(_r(alerts=6, fb=0)) == "ok"


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
