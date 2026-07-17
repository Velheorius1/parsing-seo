"""Regression guard for the auto-mute read resilience (2026-07-16 fix).

THE BUG: get_active_mutes did `except Exception: return set()` — a silent fail-open. On a
transient Supabase read error (statement timeout 57014 under crawl load) it returned {},
so that crawl muted NOTHING and every muted source pushed. Invisible (no log; [Route] only
logged when a digest existed). Weeks-old mutes leaked ~100% of muted-source alerts to push.

THE FIX: retry the read; on total failure fall back to the disk-persisted last-known-good
set — NEVER empty. This test locks that in: a raising/None read must NOT collapse to empty
once a good set has been cached, and the ✅-veto (pos>0) filter still holds.

Hermetic: stubs crawler.core.db + crawler.auth.session_store; cache path → a tempfile.
Run: python3 -m crawler.tests.test_mute_resilience   (exit 1 on any failure)
"""
import sys
import tempfile
import types

# Stub the DB dep imported at feedback.py module load, and the lazily-imported session_store.
_db = types.ModuleType("crawler.core.db")
_db._get_client = lambda: None
sys.modules["crawler.core.db"] = _db
_ss = types.ModuleType("crawler.auth.session_store")
_ss.session_store = types.SimpleNamespace(get_setting=lambda k: None)
sys.modules["crawler.auth.session_store"] = _ss

import crawler.core.feedback as F  # noqa: E402

F._MUTE_CACHE_FILE = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
F.time = types.SimpleNamespace(sleep=lambda s: None)  # skip real backoff in tests

_GOOD = {"sources": {"TG: Мин сельхоз": {"neg": 25, "pos": 0},
                     "ETender Обсуждения": {"neg": 21, "pos": 0},
                     "Has A Veto": {"neg": 9, "pos": 2}}}


def _set_reader(fn):
    _ss.session_store.get_setting = fn


def test_healthy_read_filters_and_caches():
    _set_reader(lambda k: _GOOD)
    m = F.get_active_mutes()
    assert m == {"TG: Мин сельхоз", "ETender Обсуждения"}, m  # pos>0 source vetoed


def test_read_exception_falls_back_to_cache_not_empty():
    _set_reader(lambda k: _GOOD)
    F.get_active_mutes()  # prime the disk cache

    def boom(k):
        raise Exception("canceling statement due to statement timeout")
    _set_reader(boom)
    m = F.get_active_mutes()
    assert m == {"TG: Мин сельхоз", "ETender Обсуждения"}, ("must serve cache, not empty", m)


def test_non_dict_read_falls_back_to_cache():
    _set_reader(lambda k: _GOOD)
    F.get_active_mutes()  # prime cache
    _set_reader(lambda k: None)  # get_setting returns None (missing/parse-fail)
    m = F.get_active_mutes()
    assert len(m) == 2, ("None read must not collapse to empty", m)


def test_veto_wins_over_negatives():
    # A single ✅ (pos>0) keeps a source OUT of the mute set regardless of ❌ count.
    _set_reader(lambda k: {"sources": {"Loud But Vetoed": {"neg": 99, "pos": 1}}})
    assert F.get_active_mutes() == set()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, "-", e)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
