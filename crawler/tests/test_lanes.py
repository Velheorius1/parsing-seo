"""Отдельная очередь источников XT-Xarid (10.10.2026).

Зачем. С 07.10 бэкенд XT-Xarid/Hayotbirja пускает с нашего IP один запрос в
минуту. Основной краул ждёт все источники, прежде чем слать алерты, поэтому
источник этого бэкенда внутри него задержал бы алерты всех площадок на
десятки минут. Такие источники помечены `lane: xt-backend` и идут своим cron
(`crawler.main --lane xt-backend`), а `run_crawl.sh` их пропускает.

Здесь держится:
  • выбор «кому пора» по `every_minutes` с запасом на дрожание cron;
  • любой включённый источник, который ходит в бэкенд XT, стоит в очереди
    (иначе он снова устроит пачку запросов внутри основного краула);
  • на бэкенд XT нет включённых SPA-источников: страница в браузере делает
    несколько запросов мимо общей очереди;
  • список основного краула из run_crawl.sh действительно без очереди XT.

Run: python3 -m crawler.tests.test_lanes   (exit 1 on any failure)
"""
import os
import re
import subprocess
import sys
import tempfile
import types

import yaml

from crawler.core import host_budget, lanes

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CFG = os.path.join(ROOT, "crawler", "config", "sources.yaml")
RAW = yaml.safe_load(open(CFG))["sources"]
LANE = "xt-backend"


def _src(sid, every=None, lane=LANE):
    return types.SimpleNamespace(id=sid, every_minutes=every, lane=lane)


def test_source_without_every_runs_each_time():
    assert lanes.due_ids([_src("red")], {"red": 999.0}, now=1000.0) == ["red"]


def test_never_ran_is_due():
    assert lanes.due_ids([_src("tender", 120)], {}, now=1000.0) == ["tender"]


def test_not_due_before_interval():
    now = 100000.0
    state = {"tender": now - 60 * 60}
    assert lanes.due_ids([_src("tender", 120)], state, now=now) == []


def test_cron_jitter_does_not_skip_a_whole_cycle():
    # Прогон в 10:00:40 после прогона в 08:01:30 — это «пора», а не «через 2 ч 20 мин».
    now = 100000.0
    state = {"tender": now - (120 * 60 - 50)}
    assert lanes.due_ids([_src("tender", 120)], state, now=now) == ["tender"]


def test_order_follows_config():
    srcs = [_src("a", 120), _src("b"), _src("c", 720)]
    assert lanes.due_ids(srcs, {}, now=1000.0) == ["a", "b", "c"]


def test_lane_sources_filters_by_lane():
    srcs = [_src("a"), _src("b", lane=None), _src("c", lane="other")]
    assert [s.id for s in lanes.lane_sources(srcs, LANE)] == ["a"]


def test_state_roundtrip_and_corrupt_file():
    d = tempfile.mkdtemp(prefix="lanes-")
    lanes.save_state(LANE, lanes.mark_ran({}, ["a", "b"], now=123.0), state_dir=d)
    assert lanes.load_state(LANE, state_dir=d) == {"a": 123.0, "b": 123.0}
    with open(lanes.state_path(LANE, d), "w") as f:
        f.write("[broken")
    assert lanes.load_state(LANE, state_dir=d) == {}


def test_every_enabled_xt_backend_source_is_in_the_lane():
    offenders = [s["id"] for s in RAW
                 if s.get("enabled", True) and host_budget.backend_for(s.get("url")) == host_budget.XT_BACKEND
                 and s.get("lane") != LANE]
    assert not offenders, "источники бэкенда XT вне очереди: %s" % offenders


def test_no_enabled_spa_on_xt_backend():
    spa = [s["id"] for s in RAW
           if s.get("enabled", True) and s.get("adapter") == "spa"
           and host_budget.backend_for(s.get("url")) == host_budget.XT_BACKEND]
    assert not spa, spa


def test_lane_keeps_the_valuable_sources():
    # Hayotbirja отбор — единственный источник этого бэкенда, по которому подавали
    # заявки (UNICON, 638 млн); выключить его вместе с дублями было бы потерей.
    by_id = {s["id"]: s for s in RAW}
    for sid in ("hayotbirja-selection", "xt-xarid-reduction", "xt-xarid-tender",
                "xt-xarid-request-proposals"):
        assert by_id[sid].get("enabled", True) is True, sid
        assert by_id[sid].get("lane") == LANE, sid


def test_lane_fits_the_platform_budget():
    # Площадка: 60 запросов в час. Очередь не должна просить больше половины —
    # остальное сторожу наших лотов, верификатору и запасу на 429.
    lane_cron_minutes = 20
    per_day = 0.0
    for s in RAW:
        if not s.get("enabled", True) or s.get("lane") != LANE:
            continue
        pages = (s.get("pagination") or {}).get("max_pages", 10)
        every = s.get("every_minutes") or lane_cron_minutes
        per_day += pages * (24 * 60.0 / max(every, lane_cron_minutes))
    assert per_day <= 24 * 60 / 2, per_day


def test_run_crawl_main_list_skips_lane_sources():
    # Исполняем ровно тот python, которым run_crawl.sh собирает список основного краула.
    sh = open(os.path.join(ROOT, "scripts", "run_crawl.sh")).read()
    block = re.search(r'--no-telegram" \]; then\s+EXTRA_ARGS="--sources \$\(\$VENV -c "(.*?)"\)"', sh, re.S)
    assert block, "не нашёл python-блок --no-telegram в run_crawl.sh"
    code = block.group(1).replace("$DIR", ROOT)
    out = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT).decode().split()
    lane_ids = {s["id"] for s in RAW if s.get("lane")}
    assert lane_ids, "в конфиге нет источников очереди — тест ничего не проверит"
    assert not (set(out) & lane_ids), set(out) & lane_ids
    assert "etender" in out  # список вообще собирается


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
