"""healthcheck: Cooperation по журналу прогонов, каждый раздел отдельно (21.10.2026).

ЧТО БЫЛО. За 60 дней 60 из 181 прогона Cooperation не довели лоты до базы
(прокси 402, сбой БД 10–22.09), а geo.cooperationuz — свежесть всех источников
Cooperation разом и только WARN — показывал OK. «Аукционы» и «Э-магазин лоты»
стояли в белом списке мёртвых как «заменены», хотя оба живые разделы спроса;
«E-Birja активные аукционы» — в выведенных, хотя включён (списки чинятся
отдельно, вместе с правками ядра).

Здесь держится: сбой, неполный снимок и тишина дольше 13 ч у раздела спроса —
FAIL; у оферт и контрактов — WARN; нет журнала — FAIL.

Run: .venv/bin/python3 -m pytest crawler/tests/test_coop_runs_check.py -q
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.scripts.healthcheck as H  # noqa: E402

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)


def _ev(section, hours_ago=1, **kw):
    ev = {"ts": (NOW - timedelta(hours=hours_ago)).isoformat(), "section": section, "status": "ok",
          "fetched": 10, "total": 10, "new": 2, "alerts": 0, "error": "",
          "source": {"Lots": "Cooperation.uz Лоты", "EshopLots": "Cooperation.uz Э-магазин лоты",
                     "AuctionLots": "Cooperation.uz Аукционы"}.get(section, section)}
    ev.update(kw)
    return ev


def _check(events):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    try:
        hc = H.HealthCheck()
        hc.check_coop_runs(now=NOW, path=path)
    finally:
        os.unlink(path)
    return {r["component"]: (r["status"], r["message"]) for r in hc.results}


def _all_ok(**override):
    evs = [_ev("Lots"), _ev("EshopLots"), _ev("AuctionLots")]
    return evs + [override["extra"]] if "extra" in override else evs


def test_all_demand_sections_fresh_and_complete_is_ok():
    res = _check(_all_ok())
    assert {k: v[0] for k, v in res.items()} == {"coop.Lots": "ok", "coop.EshopLots": "ok",
                                                 "coop.AuctionLots": "ok"}, res


def test_failed_section_is_fail_with_the_reason():
    res = _check(_all_ok(extra=_ev("Lots", status="failed", error="page 1: FetchFailed: 402")))
    assert res["coop.Lots"][0] == "fail" and "402" in res["coop.Lots"][1], res


def test_truncated_snapshot_is_fail():
    res = _check(_all_ok(extra=_ev("EshopLots", status="partial", error="took 10 of 302")))
    assert res["coop.EshopLots"][0] == "fail" and "10 of 302" in res["coop.EshopLots"][1], res


def test_silence_longer_than_a_night_is_fail():
    res = _check([_ev("Lots", hours_ago=14), _ev("EshopLots"), _ev("AuctionLots")])
    assert res["coop.Lots"][0] == "fail" and "14.0 ч" in res["coop.Lots"][1], res
    assert _check([_ev("Lots", hours_ago=12), _ev("EshopLots"), _ev("AuctionLots")])["coop.Lots"][0] == "ok"


def test_non_demand_section_failure_is_only_warn():
    res = _check(_all_ok(extra=_ev("AucContracts", status="failed", error="handshake timed out")))
    assert res["coop.AucContracts"][0] == "warn", res


def test_missing_journal_is_fail():
    hc = H.HealthCheck()
    hc.check_coop_runs(now=NOW, path="/nonexistent/coop_runs.jsonl")
    assert [(r["component"], r["status"]) for r in hc.results] == [("coop.runs", "fail")], hc.results


def test_section_never_seen_is_fail():
    res = _check([_ev("Lots"), _ev("EshopLots")])
    assert res["coop.AuctionLots"][0] == "fail", res



def test_proxy_skip_is_written_to_the_journal_as_a_failure():
    sh = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "scripts", "run_proxy_fetch.sh"), encoding="utf-8").read()
    skip = sh[sh.index("SKIP proxy fetch"):sh.index("exit 0", sh.index("SKIP proxy fetch"))]
    assert "coop_runs.jsonl" in skip and '"status": "failed"' in skip, skip
    for sec in H.COOP_DEMAND_SECTIONS:
        assert sec in skip, sec
