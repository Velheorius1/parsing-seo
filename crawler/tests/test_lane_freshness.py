"""Healthcheck и отдельные очереди источников (10.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. PR #85 вынес пять источников бэкенда XT-Xarid в свою очередь
(`lane: xt-backend`, cron `crawler.main --lane`), а основной прогон стал идти
без них. `freshness.full_api` же ждал прогон ровно по всем включённым
не-Telegram источникам — совпадения не стало, и с 04:00 висел FAIL «No
completed full API crawl in the last 100 runs» при живом краулере.

Здесь закреплено: полный прогон — это профиль `run_crawl.sh --no-telegram`
(без очередей); свежесть очереди проверяется отдельно, по её отметкам, и
остановка очереди — FAIL с именем источника.

Run: .venv/bin/python3 -m pytest crawler/tests/test_lane_freshness.py -q
"""
import io
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.scripts.healthcheck as H  # noqa: E402
from crawler.core import lanes  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sources():
    with io.open(os.path.join(_ROOT, "config", "sources.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)["sources"]


def _main_profile():
    """Ровно то, что передаёт `scripts/run_crawl.sh --no-telegram`."""
    return [s["id"] for s in _sources()
            if s.get("enabled", True) and s.get("adapter") != "telegram" and not s.get("lane")]


def _lane_items():
    return [s for s in _sources() if s.get("enabled", True) and s.get("lane") == "xt-backend"]


def _results(hc, component):
    return [r for r in hc.results if r["component"] == component]


def _run(source_filter, hours_ago=1.0, errors=0):
    started = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"started_at": started.isoformat(), "source_filter": source_filter,
            "errors_count": errors, "total_fetched": 10, "total_new": 1}


def test_main_crawl_without_lane_sources_counts_as_full():
    hc = H.HealthCheck()
    hc._check_full_api_freshness([_run(_main_profile())])
    [res] = _results(hc, "freshness.full_api")
    assert res["status"] == H.OK, res["message"]


def test_lane_only_runs_are_not_mistaken_for_a_full_crawl():
    hc = H.HealthCheck()
    hc._check_full_api_freshness([_run([s["id"] for s in _lane_items()])])
    [res] = _results(hc, "freshness.full_api")
    assert res["status"] == H.FAIL
    assert "No completed full API crawl" in res["message"]


def test_config_really_has_lane_sources_outside_the_main_profile():
    # Без этого первые два теста прошли бы и на старом коде.
    lane_ids = set(s["id"] for s in _lane_items())
    assert lane_ids, "в sources.yaml нет источников lane: xt-backend"
    assert not lane_ids & set(_main_profile())


def test_stale_after_minutes_covers_tick_skip_and_run_time():
    assert H.lane_stale_after_minutes(None) == 100
    assert H.lane_stale_after_minutes(120) == 300
    assert H.lane_stale_after_minutes(720) == 1500


def _state_dir_with(marks):
    d = tempfile.mkdtemp(prefix="lane-hc-")
    lanes.save_state("xt-backend", marks, state_dir=d)
    return d


def test_fresh_lane_is_ok():
    now = time.time()
    d = _state_dir_with(dict((s["id"], now - 10 * 60) for s in _lane_items()))
    hc = H.HealthCheck()
    hc.check_lane_freshness(now=now, state_dir=d)
    [res] = _results(hc, "freshness.lane")
    assert res["status"] == H.OK, res["message"]
    assert "xt-backend" in res["message"]


def test_stopped_lane_fails_with_the_source_name():
    now = time.time()
    items = _lane_items()
    marks = dict((s["id"], now - 10 * 60) for s in items)
    every_run = next(s for s in items if not s.get("every_minutes"))
    marks[every_run["id"]] = now - 3 * 3600  # 180 мин при норме 100
    d = _state_dir_with(marks)
    hc = H.HealthCheck()
    hc.check_lane_freshness(now=now, state_dir=d)
    [res] = _results(hc, "freshness.lane")
    assert res["status"] == H.FAIL
    assert every_run["id"] in res["message"]


def test_slow_source_inside_its_own_interval_is_not_stale():
    now = time.time()
    items = _lane_items()
    slow = max(items, key=lambda s: s.get("every_minutes") or 0)
    assert (slow.get("every_minutes") or 0) >= 120
    marks = dict((s["id"], now - 10 * 60) for s in items)
    marks[slow["id"]] = now - slow["every_minutes"] * 60  # ровно один интервал назад
    d = _state_dir_with(marks)
    hc = H.HealthCheck()
    hc.check_lane_freshness(now=now, state_dir=d)
    [res] = _results(hc, "freshness.lane")
    assert res["status"] == H.OK, res["message"]


def test_missing_state_file_warns_instead_of_passing():
    d = tempfile.mkdtemp(prefix="lane-hc-empty-")
    hc = H.HealthCheck()
    hc.check_lane_freshness(now=time.time(), state_dir=d)
    [res] = _results(hc, "freshness.lane")
    assert res["status"] == H.WARN
    assert "нет отметок" in res["message"]


def test_source_that_never_ran_warns():
    now = time.time()
    items = _lane_items()
    marks = dict((s["id"], now - 10 * 60) for s in items[1:])
    d = _state_dir_with(marks)
    hc = H.HealthCheck()
    hc.check_lane_freshness(now=now, state_dir=d)
    [res] = _results(hc, "freshness.lane")
    assert res["status"] == H.WARN
    assert items[0]["id"] in res["message"]


def test_main_runs_the_lane_check():
    src = io.open(os.path.join(_ROOT, "scripts", "healthcheck.py"), encoding="utf-8").read()
    assert "hc.check_lane_freshness()" in src
