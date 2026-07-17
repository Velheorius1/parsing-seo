"""Regression guard for the docker decommissioned/stale-crawler check (2026-07-17 incident).

THE INCIDENT: a duplicate container `tender-crawler` (image built 2026-06-07) ran a full
crawl every 2h and alerted with code predating auto-mute / 3-tier routing / e-shop demote /
V1 verifier. It out-raced the cron crawler, so the fixed path never routed those tenders —
weeks-old mutes still pushed ~100%. Its code is baked into the image, so git auto-deploy
could never reach it. Worse: check_docker EXPECTED it running and warned when MISSING —
the guard pointed the wrong way. The systemd stale-guard never saw it (units only).

These tests lock the inverted contract in: a decommissioned crawler running is a FAIL, a
clean host is OK, and any container shipping crawler code older than prod is a FAIL.

Run: python3 -m crawler.tests.test_docker_stale_guard   (exit 1 on any failure)
"""
import subprocess
import sys
import types

from crawler.scripts.healthcheck import FAIL, OK, HealthCheck


class _Fake(object):
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _hc(ps_output, stale=None):
    """HealthCheck with docker ps stubbed; _stale_crawler_containers stubbed to `stale`."""
    hc = HealthCheck()
    import crawler.scripts.healthcheck as H
    H.subprocess = types.SimpleNamespace(
        run=lambda *a, **k: _Fake(ps_output),
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    hc._stale_crawler_containers = lambda running: list(stale or [])
    return hc


def _last(hc):
    return [r for r in hc.results if r["component"] == "docker"][-1]


def test_decommissioned_crawler_running_is_FAIL():
    # The exact 2026-07-17 bug: its presence must scream, not reassure.
    hc = _hc("tender-crawler\nbrain-bot\n")
    hc.check_docker()
    r = _last(hc)
    assert r["status"] == FAIL, r
    assert "tender-crawler" in r["message"]


def test_clean_host_is_OK():
    hc = _hc("brain-bot\nwinch-bot\nsalesbot-v3\n")
    hc.check_docker()
    assert _last(hc)["status"] == OK


def test_stale_code_container_is_FAIL():
    # A NEW frozen-image crawler (not on the decommissioned list) must still be caught.
    hc = _hc("some-new-crawler\n", stale=["some-new-crawler"])
    hc.check_docker()
    r = _last(hc)
    assert r["status"] == FAIL, r
    assert "older than prod" in r["message"]


def test_decommissioned_takes_precedence_and_short_circuits():
    # Rogue check runs first and returns — no double-reporting.
    hc = _hc("tender-crawler\n", stale=["tender-crawler"])
    hc.check_docker()
    docker_results = [r for r in hc.results if r["component"] == "docker"]
    assert len(docker_results) == 1, docker_results
    assert docker_results[0]["status"] == FAIL


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, "-", str(e)[:120])
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
