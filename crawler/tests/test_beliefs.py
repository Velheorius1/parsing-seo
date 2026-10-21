"""Реестр «аксиом» и их перепроверка (21.10.2026).

ЧТО БЫЛО. Старые аукционы UZEX три месяца не собирались: 27.07 по выборке
маленьких номеров их записали в «legacy», 20.10 повторили запись 11.06 «список
закрыт токеном» — оба раза без перепроверки, хотя список давно открыт (621 лот).
Аудит 21.10 нашёл ещё 9 таких «фактов» без даты и пробы.

Здесь держится: реестр crawler/config/beliefs.yaml в порядке (у каждой записи —
откуда, что из-за неё не делаем, проба или дата ручной перепроверки); проба
различает «держится / не держится», переворот записывается только после второй
пробы; healthcheck: переворот — FAIL, давняя проба и наступивший review_by — WARN.

Run: .venv/bin/python3 -m pytest crawler/tests/test_beliefs.py -q
"""
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import httpx
import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.scripts.belief_probe as BP  # noqa: E402
import crawler.scripts.healthcheck as H  # noqa: E402

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
_EXPECT_KINDS = {"unreachable", "status_in", "json_list_min", "json_keys_absent"}


# ── реестр ──

def test_registry_entries_are_complete():
    beliefs = BP.load_beliefs()
    ids = [b["id"] for b in beliefs]
    assert len(ids) == len(set(ids)), "повтор id"
    assert len(beliefs) >= 10
    for b in beliefs:
        for f in ("claim", "because", "recorded"):
            assert b.get(f), "%s: нет %s" % (b["id"], f)
        if b.get("probe"):
            p = b["probe"]
            assert p["url"].startswith("http"), b["id"]
            assert set(p["expect"]) and set(p["expect"]) <= _EXPECT_KINDS, b["id"]
            assert b.get("recheck_days"), b["id"]
            assert b.get("blind"), "%s: не записано, чего проба не видит" % b["id"]
        else:
            assert datetime.strptime(str(b["review_by"]), "%Y-%m-%d").date() > date(2026, 10, 21), b["id"]


# ── вердикт пробы ──

def test_judge_unreachable():
    assert BP.judge({"unreachable": True}, None, None)[0] is True
    assert BP.judge({"unreachable": True}, 200, "<html>")[0] is False


def test_judge_status_in():
    assert BP.judge({"status_in": [403, 503]}, 403, "")[0] is True
    assert BP.judge({"status_in": [403, 503]}, 200, "")[0] is False
    assert BP.judge({"status_in": [403]}, None, None)[0] is False


def test_judge_json_list_min_with_path():
    body = json.dumps({"result": {"data": [{"a": 1}, {"a": 2}]}})
    assert BP.judge({"json_list_min": 2}, 200, body, "result.data") == (True, "HTTP 200, список 2")
    assert BP.judge({"json_list_min": 1}, 200, "[]")[0] is False
    assert BP.judge({"json_list_min": 1}, 500, "[]")[0] is False
    assert BP.judge({"json_list_min": 1}, 200, "<html>")[0] is False


def test_judge_json_keys_absent():
    rows = json.dumps([{"price": 1, "seller": "x"}])
    assert BP.judge({"json_keys_absent": ["customer"]}, 200, rows)[0] is True
    holds, seen = BP.judge({"json_keys_absent": ["customer", "seller"]}, 200, rows)
    assert holds is False and "seller" in seen
    assert BP.judge({"json_keys_absent": ["customer"]}, 200, "[]")[0] is False


# ── прогон: переворот только после второй пробы ──

def _registry(tmp, beliefs):
    path = os.path.join(tmp, "beliefs.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"beliefs": beliefs}, fh, allow_unicode=True)
    return path


def _list_belief(bid="list-open"):
    return {"id": bid, "claim": "c", "because": "b", "recorded": "r", "recheck_days": 7, "blind": "x",
            "probe": {"method": "GET", "url": "https://example.test/list", "expect": {"json_list_min": 1}}}


def _client(bodies):
    seq = iter(bodies)

    def handler(request):
        return httpx.Response(200, text=next(seq))
    return httpx.Client(transport=httpx.MockTransport(handler))


def _run(beliefs, bodies, old_state=None):
    tmp = tempfile.mkdtemp()
    state_path = os.path.join(tmp, "state.json")
    if old_state is not None:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(old_state, fh)
    state = BP.run(beliefs_path=_registry(tmp, beliefs), state_path=state_path,
                   client=_client(bodies), confirm_pause=0)
    with open(state_path, "r", encoding="utf-8") as fh:
        assert json.load(fh) == state
    return state


def test_single_glitch_does_not_flip():
    st = _run([_list_belief()], ["[]", "[1]"])["list-open"]
    assert st["holds"] is True and "повтор" in st["observed"]


def test_flip_recorded_after_second_probe():
    st = _run([_list_belief()], ["[]", "[]"])["list-open"]
    assert st["holds"] is False and "повтор: HTTP 200, список 0" in st["observed"]


def test_holding_belief_probed_once():
    st = _run([_list_belief()], ["[1]"])["list-open"]
    assert st == {"checked_at": st["checked_at"], "holds": True, "observed": "HTTP 200, список 1"}


def test_removed_beliefs_dropped_from_state():
    state = _run([_list_belief()], ["[1]"], old_state={"gone": {"holds": False}})
    assert set(state) == {"list-open"}


def test_unreachable_probe_holds_on_connect_error():
    def handler(request):
        raise httpx.ConnectTimeout("timeout", request=request)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    b = {"probe": {"url": "https://example.test/", "expect": {"unreachable": True}}}
    holds, seen = BP.probe(b, client)
    assert holds is True and "ConnectTimeout" in seen


# ── healthcheck ──

def _hc(beliefs, state):
    tmp = tempfile.mkdtemp()
    state_path = os.path.join(tmp, "state.json")
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    hc = H.HealthCheck()
    hc.check_beliefs(now=NOW, beliefs_path=_registry(tmp, beliefs), state_path=state_path)
    return {r["component"]: (r["status"], r["message"]) for r in hc.results}


def _st(holds=True, days_ago=1):
    return {"checked_at": (NOW - timedelta(days=days_ago)).isoformat(), "holds": holds, "observed": "HTTP 200"}


def test_flipped_belief_is_fail():
    res = _hc([_list_belief()], {"list-open": _st(holds=False)})
    assert res["beliefs.list-open"][0] == "fail"
    assert "пересмотреть" in res["beliefs.list-open"][1]


def test_stale_probe_is_warn():
    res = _hc([_list_belief()], {"list-open": _st(days_ago=10)})
    assert res["beliefs.list-open"][0] == "warn"


def test_never_probed_is_warn():
    assert _hc([_list_belief()], {})["beliefs.list-open"][0] == "warn"


def test_manual_review_due_is_warn_and_future_is_quiet():
    due = {"id": "m1", "claim": "c", "because": "b", "recorded": "r", "review_by": "2026-10-01"}
    later = dict(due, id="m2", review_by="2027-01-21")
    res = _hc([due, later], {})
    assert res["beliefs.m1"][0] == "warn"
    assert "beliefs.m2" not in res


def test_all_good_is_one_ok_line():
    later = {"id": "m2", "claim": "c", "because": "b", "recorded": "r", "review_by": "2027-01-21"}
    res = _hc([_list_belief(), later], {"list-open": _st()})
    assert res == {"beliefs": ("ok", "аксиом подтверждено пробой: 1, на ручной перепроверке: 1")}


def test_real_registry_with_empty_state_has_no_fail():
    hc = H.HealthCheck()
    hc.check_beliefs(now=NOW, state_path=os.path.join(tempfile.mkdtemp(), "none.json"))
    assert not [r for r in hc.results if r["status"] == "fail"], hc.results
