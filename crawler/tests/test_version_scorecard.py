"""Scoring math + corpus schema guards for the version benchmark (2026-07-27).

The benchmark's job is to make a regression impossible to miss and a
non-regression impossible to mistake for one. Both halves are pinned here:
the arithmetic (recall/precision/routing/prefilter over expect_*), and the
noise handling that keeps a flaky OpenRouter from reading as "the crawler got
dumber".

Run: python3 -m crawler.tests.test_version_scorecard   (exit 1 on any failure)
"""
import json
import os
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="печать,упаковка,коробка,этикетка,издательск,стикер,флаер",
        ai_score_threshold=70, ai_relevance_model="max", ai_relevance_model_fast="fast",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.scripts.version_scorecard import (  # noqa: E402
    CORPUS, WEIGHTS, _fmt_tg, _score, _to_tender,
)
from crawler.scripts.replay import ReplayVerdict  # noqa: E402


def _e(cid, deliver, kind="pipeline", route=None):
    return {"cid": cid, "kind": kind, "expect_delivered": deliver,
            "expect_route": route, "external_id": cid, "title": "t"}


def _v(delivered, passed=True, route=None, ai_error=False, stage=None):
    return ReplayVerdict(
        external_id="x", source="s", title="t", passed_prefilter=passed,
        dropped_at_stage=stage, matched_kw="kw", uzex_bypass=False, is_lead=False,
        ai_error=ai_error, ai_skipped=False, delivered=delivered, route=route)


# ── arithmetic ───────────────────────────────────────────────────────────────

def test_perfect_corpus_scores_ten():
    pairs = [(_e("a", True), _v(True)), (_e("b", False), _v(False, passed=False))]
    r = _score(pairs)
    assert r["score"] == 10.0, r
    assert r["misses"] == []


def test_one_missed_relevant_costs_exactly_its_recall_weight():
    """AI-stage miss: it passed the deterministic gates, so ONLY recall moves."""
    pairs = [(_e("r%d" % i, True, kind="ai_only"), _v(True)) for i in range(9)]
    pairs.append((_e("miss", True, kind="ai_only"), _v(False, passed=True, stage=None)))
    r = _score(pairs)
    # recall 0.9, everything else 1.0 → 10*(1 - 0.4*0.1) = 9.6
    assert r["components"]["recall"] == 0.9
    assert r["components"]["prefilter"] == 1.0
    assert r["score"] == 9.6, r["score"]


def test_prefilter_stage_miss_is_penalized_twice_on_purpose():
    """A relevant lot killed by a deterministic gate is a CODE regression: it
    costs recall AND prefilter, so it outranks an AI wobble in the score."""
    pairs = [(_e("r%d" % i, True) , _v(True)) for i in range(9)]
    pairs.append((_e("miss", True), _v(False, passed=False, stage="min_price")))
    r = _score(pairs)
    assert r["components"]["recall"] == 0.9 and r["components"]["prefilter"] == 0.9
    # 10*(0.4*0.9 + 0.3 + 0.15 + 0.15*0.9) = 9.45 → 9.5
    assert r["score"] == 9.5, r["score"]
    assert r["misses"] == [{"cid": "miss", "stage": "min_price"}]


def test_false_positive_hits_precision_only():
    pairs = [(_e("a", True), _v(True)),
             (_e("n1", False), _v(False)), (_e("n2", False), _v(True))]
    r = _score(pairs)
    assert r["components"]["precision"] == 0.5
    assert r["components"]["recall"] == 1.0
    assert {"cid": "n2", "stage": "false-positive"} in r["misses"]


def test_digest_instead_of_push_hits_routing_not_recall():
    """Caught, but in the wrong tier: one soft penalty, not two."""
    pairs = [(_e("a", True, route="push"), _v(True, route="digest"))]
    r = _score(pairs)
    assert r["components"]["recall"] == 1.0
    assert r["components"]["routing"] == 0.0
    assert r["score"] == round(10 * (1 - WEIGHTS["routing"]), 1)


def test_prefilter_component_is_code_only():
    """It must move on deterministic stages alone, so it still works when AI is down."""
    pairs = [(_e("a", True), _v(True, passed=True)),
             (_e("b", True), _v(True, passed=False))]
    assert _score(pairs)["components"]["prefilter"] == 0.5


def test_ai_only_entries_do_not_touch_prefilter_component():
    pairs = [(_e("g", True, kind="ai_only"), _v(True, passed=False))]
    assert _score(pairs)["components"]["prefilter"] == 1.0


def test_empty_class_defaults_to_one_not_zero():
    """A corpus with no drop-expected entries must not score 0 on precision."""
    r = _score([(_e("a", True), _v(True))])
    assert r["components"]["precision"] == 1.0 and r["score"] == 10.0


# ── noise handling ───────────────────────────────────────────────────────────

def test_transport_errors_leave_the_denominators():
    pairs = [(_e("a", True), _v(True)),
             (_e("b", True), _v(True, ai_error=True))]
    r = _score(pairs)
    assert r["n_scored"] == 1 and r["n_entries"] == 2
    assert r["components"]["recall"] == 1.0
    assert r["ai_error_rate"] == 0.5
    assert r["fail_open_delivered"] == 1


def test_degraded_run_reports_no_delta():
    rec = {"score": 6.0, "components": {"recall": .5, "precision": .5, "routing": 1., "prefilter": 1.},
           "n_entries": 10, "n_scored": 4, "ai_error_rate": 0.6, "degraded": True,
           "corpus_version": "v1", "git_sha": "aaa", "misses": [],
           "config_fingerprint": {}}
    prev = dict(rec, score=9.5, degraded=False, git_sha="bbb")
    out = _fmt_tg(rec, prev)
    assert "НЕ сравним" in out and "-3.5" not in out


def test_red_flag_separates_code_from_config_drift():
    base = {"components": {"recall": .5, "precision": 1., "routing": 1., "prefilter": 1.},
            "n_entries": 10, "n_scored": 10, "ai_error_rate": 0.0, "degraded": False,
            "corpus_version": "v1", "misses": []}
    prev = dict(base, score=9.5, git_sha="aaa", config_fingerprint={"playbook_sha1": "old"})
    same_code = dict(base, score=8.0, git_sha="aaa", config_fingerprint={"playbook_sha1": "new"})
    assert "конфиг" in _fmt_tg(same_code, prev)

    same_all = dict(base, score=8.0, git_sha="aaa", config_fingerprint={"playbook_sha1": "old"})
    assert "шум провайдера" in _fmt_tg(same_all, prev)

    new_code = dict(base, score=8.0, git_sha="ccc", config_fingerprint={"playbook_sha1": "old"})
    assert "деплой" in _fmt_tg(new_code, prev)


def test_rebaseline_suppresses_the_delta():
    base = {"components": {"recall": 1., "precision": 1., "routing": 1., "prefilter": 1.},
            "n_entries": 10, "n_scored": 10, "ai_error_rate": 0.0, "degraded": False,
            "misses": [], "git_sha": "aaa", "config_fingerprint": {}}
    rec = dict(base, score=8.0, corpus_version="v2")
    prev = dict(base, score=9.9, corpus_version="v1")
    out = _fmt_tg(rec, prev)
    assert "rebaseline" in out and "🟥" not in out


# ── the real corpus file ─────────────────────────────────────────────────────

def test_corpus_file_is_well_formed():
    c = json.load(open(CORPUS, encoding="utf-8"))
    entries = c["entries"]
    assert c.get("corpus_version") and entries, "empty corpus"
    cids = [e["cid"] for e in entries]
    assert len(set(cids)) == len(cids), "duplicate cid"
    for e in entries:
        assert e["kind"] in ("pipeline", "ai_only", "lead"), e["cid"]
        assert e["label"] in ("relevant", "irrelevant"), e["cid"]
        assert isinstance(e["expect_delivered"], bool), e["cid"]
        assert e.get("expect_route") in (None, "push", "digest"), e["cid"]
        assert e.get("provenance"), e["cid"]
        # frozen_now is what keeps the corpus from rotting: without it every
        # dated entry expires within a year and "recall fell" is the calendar.
        if e.get("deadline"):
            assert e.get("frozen_now"), "dated entry without frozen_now: %s" % e["cid"]


def test_every_corpus_entry_builds_a_tender():
    c = json.load(open(CORPUS, encoding="utf-8"))
    for e in c["entries"]:
        t = _to_tender(e)
        assert t.external_id and isinstance(t.extra_info, dict), e["cid"]


def test_corpus_has_both_classes_in_useful_numbers():
    c = json.load(open(CORPUS, encoding="utf-8"))
    live = [e for e in c["entries"] if not e.get("retired")]
    yes = len([e for e in live if e["expect_delivered"]])
    no = len(live) - yes
    assert yes >= 15 and no >= 15, (yes, no)


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
