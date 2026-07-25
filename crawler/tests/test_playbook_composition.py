"""Guard: recall-side principles always reach the relevance prompt (2026-07-25).

THE HOLE: get_relevance_playbook took top-N by support_count DESC. Rejection
principles accumulate support daily (every ❌ click), recall principles almost never
(a MISSED tender is only noticed if Daniyar happens to spot it elsewhere). So the
ordering starved the recall side permanently — on 2026-07-25 all 23 active principles
were rejection-side, and the one genuine recall guard ranked below the cut, i.e.
promoting it would have changed nothing. That is one-directional learning drifting
into over-rejection: exactly what the two-way feedback signal (A1) exists to stop.

These tests pin the composition contract: recall first (always), rest fills up to limit.

Run: python3 -m crawler.tests.test_playbook_composition   (exit 1 on any failure)
"""
import sys
import types


def _load():
    for name in ("crawler.core.db", "crawler.auth.session_store"):
        if name not in sys.modules:
            m = types.ModuleType(name)
            if name.endswith("db"):
                m._get_client = lambda: None
            else:
                m.session_store = types.SimpleNamespace(get_setting=lambda k: None,
                                                        set_setting=lambda k, v: None)
            sys.modules[name] = m
    import crawler.core.feedback as F
    return F


F = _load()


def _rows(n_reject, n_recall):
    """Rejection principles carry high support, recall ones the minimum — the real shape."""
    out = [{"taxonomy": "irrelevant-niche", "principle": "rej%d" % i, "example": "",
            "support_count": 100 - i} for i in range(n_reject)]
    out += [{"taxonomy": F._RECALL_TAXONOMY, "principle": "recall%d" % i, "example": "",
             "support_count": 1} for i in range(n_recall)]
    return out


def _compose(rows, limit=20):
    """Mirror of the composition step, fed by a stubbed client."""
    class _Resp(object):
        data = rows

    class _Q(object):
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def order(self, *a, **k): return self
        def execute(self): return _Resp()

    class _C(object):
        def table(self, *a, **k): return _Q()

    F._get_client = lambda: _C()
    F._playbook_cache = None          # defeat the 2h cache between cases
    F._playbook_cache_ts = 0
    return F.get_relevance_playbook(limit=limit)


def test_recall_survives_a_crowded_rejection_set():
    # The 2026-07-25 shape: 23 rejection principles with far higher support + 2 recall.
    out = _compose(_rows(23, 2))
    assert "recall0" in out and "recall1" in out, out[-300:]
    assert out.count("\n") + 1 == 20, "limit not honored"


def test_recall_comes_first():
    out = _compose(_rows(23, 2))
    first = out.split("\n")[0]
    assert F._RECALL_TAXONOMY in first, first


def test_rejection_fills_remaining_slots_by_support():
    out = _compose(_rows(23, 2))
    # 20 slots - 2 recall = 18 rejection, taken from the top of the support order
    assert "rej0" in out and "rej17" in out, "top-support rejections missing"
    assert "rej18" not in out, "over-filled past the limit"


def test_no_recall_behaves_as_before():
    out = _compose(_rows(23, 0))
    assert out.count("\n") + 1 == 20
    assert "rej0" in out and "rej19" in out and "rej20" not in out


def test_recall_alone_is_not_padded():
    out = _compose(_rows(0, 3))
    assert out.count("\n") + 1 == 3, out


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
