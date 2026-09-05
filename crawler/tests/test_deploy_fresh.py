"""Regression guard for the deploy-freshness watchdog and its logs/ blind spot.

THE HOLE (found 2026-09-05): `logs/metrics.jsonl` was committed on 2026-04-28 even
though `.gitignore` lists `logs/` — the rule does not untrack an already-tracked
file. The nightly metrics cron appended to it, so prod's working tree was
permanently dirty. check_deploy_fresh saw that and deliberately ignored it:

    blocking = [l for l in dirty if "logs/" not in l]

The comment claimed churn under logs/ "does not block a fast-forward of other
paths". Measured on a model repo: true when upstream touches other files, FALSE
the moment any commit touches the dirty file itself — `git pull --ff-only` then
aborts with "Your local changes would be overwritten by merge" and prod freezes
on old code. The auto-deploy cron writes to its log only on success (`&& echo`)
and its stderr goes to cron mail, i.e. nowhere, so the freeze would be silent.

These tests lock in: every dirty tracked path counts, untracked never does, and
the watchdog fails loudly with the dirty file named.

Run: python3 -m crawler.tests.test_deploy_fresh   (exit 1 on any failure)
"""
import subprocess
import sys
import types

from crawler.scripts.healthcheck import FAIL, OK, HealthCheck, blocking_dirty


# ── blocking_dirty: what counts as a mine ──

def test_dirty_tracked_file_under_logs_counts():
    # THE regression: this exact line was invisible for four months.
    assert blocking_dirty(" M logs/metrics.jsonl") == ["logs/metrics.jsonl"]


def test_untracked_never_blocks():
    # "??" entries cannot stop a fast-forward — they must not raise a false FAIL.
    porcelain = "?? resend_uzex.py\n?? docs/weekly/data/2026-W36.json\n"
    assert blocking_dirty(porcelain) == []


def test_deleted_tracked_file_counts():
    assert blocking_dirty(" D crawler/core/runner.py") == ["crawler/core/runner.py"]


def test_staged_forms_parse():
    # "M  path" (staged, clean worktree) and "MM path" (both) keep the full path.
    assert blocking_dirty("M  crawler/core/db.py") == ["crawler/core/db.py"]
    assert blocking_dirty("MM crawler/core/db.py") == ["crawler/core/db.py"]


def test_leading_space_eaten_by_strip_still_parses():
    # A caller that .strip()ed git's output turns " M logs/x" into "M logs/x".
    # The old code sliced [3:] blindly and reported "ogs/metrics.jsonl" — a path
    # that does not exist, so the suggested `git checkout --` could not work.
    assert blocking_dirty("M logs/metrics.jsonl") == ["logs/metrics.jsonl"]


def test_mix_keeps_only_tracked():
    porcelain = " M logs/metrics.jsonl\n?? scratch.py\n M crawler/core/db.py\n"
    assert blocking_dirty(porcelain) == ["logs/metrics.jsonl", "crawler/core/db.py"]


def test_clean_tree_and_empty_input():
    assert blocking_dirty("") == []
    assert blocking_dirty(None) == []


# ── check_deploy_fresh: end-to-end verdicts ──

class _Fake(object):
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _hc(status_porcelain="", behind="0", head="abc1234"):
    """HealthCheck with every git call stubbed by the argument it is given."""
    hc = HealthCheck()
    import crawler.scripts.healthcheck as H

    def _run(cmd, **kwargs):
        args = list(cmd)
        if "status" in args:
            return _Fake(status_porcelain)
        if "rev-list" in args:
            return _Fake(behind)
        if "rev-parse" in args:
            return _Fake(head)
        return _Fake("")

    H.subprocess = types.SimpleNamespace(
        run=_run, TimeoutExpired=subprocess.TimeoutExpired,
    )
    return hc


def _last(hc):
    rows = [r for r in hc.results if r["component"] == "deploy_fresh"]
    assert rows, "check_deploy_fresh не отчитался вовсе"
    return rows[-1]


def test_dirty_logs_file_is_FAIL_and_names_it():
    hc = _hc(status_porcelain=" M logs/metrics.jsonl")
    hc.check_deploy_fresh()
    r = _last(hc)
    assert r["status"] == FAIL, r
    assert "logs/metrics.jsonl" in r["message"], r["message"]


def test_clean_and_current_is_OK():
    hc = _hc(status_porcelain="?? scratch.py", behind="0")
    hc.check_deploy_fresh()
    assert _last(hc)["status"] == OK, _last(hc)


def test_behind_origin_is_FAIL():
    hc = _hc(status_porcelain="", behind="3")
    hc.check_deploy_fresh()
    r = _last(hc)
    assert r["status"] == FAIL, r
    assert "3" in r["message"], r["message"]


def test_dirty_wins_over_behind_diagnosis():
    # Both symptoms at once: the message must point at the CAUSE (the dirty file),
    # not send the reader hunting the cron.
    hc = _hc(status_porcelain=" M logs/metrics.jsonl", behind="2")
    hc.check_deploy_fresh()
    msg = _last(hc)["message"]
    assert "logs/metrics.jsonl" in msg, msg
    assert "грязн" in msg, msg


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, "-", str(e)[:160])
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
