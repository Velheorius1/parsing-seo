"""Healthcheck и площадка ebirja (инцидент 01.10.2026).

ЧТО СЛУЧИЛОСЬ. С 22:00 UTC 30.09 xarid-api.ebirja.uz не отвечал никому — ни
VPS, ни Маку из Ташкента, — а api.ebirja.uz отвечал за 10,4 с. Проверка API
ждала 10 с и числила упавшим и живой, но медленный сервер, а eimzo_auth четыре
часа подряд писал «VPS cron stuck». Крон ни при чём: пока площадка лежит,
токен не обновить, и по его возрасту о кроне судить нельзя.

Эти тесты держат: медленный, но живой API — WARN, а не FAIL; проверка ждёт
не меньше 30 с; пока API ebirja лежит, eimzo_auth — UNKNOWN с причиной в среде
и не попадает в сигнатуру алерта; когда API отвечает, а токен старый — FAIL без
слова «stuck», с кроном и логом, куда смотреть.

Run: python3 -m crawler.tests.test_healthcheck_ebirja   (exit 1 on any failure)
"""
import json
import sys
import types
from datetime import datetime, timedelta, timezone

import httpx

import crawler.scripts.healthcheck as H
from crawler.scripts.healthcheck import (
    FAIL, OK, UNKNOWN, WARN, HealthCheck, api_probe_verdict, eimzo_token_verdict,
)

DOWN = ["api.ebirja-eshop", "api.ebirja-auctions"]


# ── проверка API: медленный ≠ упавший ──

def test_incident_slow_api_is_WARN_not_FAIL():
    # api.ebirja.uz 01.10: HTTP 200 за 10,4 с.
    st, msg = api_probe_verdict(200, 10.4)
    assert st == WARN, (st, msg)
    assert "медленно" in msg, msg


def test_fast_200_is_OK():
    assert api_probe_verdict(200, 0.3)[0] == OK


def test_non_200_stays_WARN():
    st, msg = api_probe_verdict(503, 0.2)
    assert (st, msg) == (WARN, "HTTP 503"), (st, msg)


class _Reply(object):
    status_code = 200


def _probe(fake_get):
    saved = httpx.get
    try:
        httpx.get = fake_get
        hc = HealthCheck()
        hc.check_api_endpoints()
        return hc.results
    finally:
        httpx.get = saved


def test_probe_waits_at_least_30s():
    seen = []

    def _get(url, timeout=None, headers=None):
        seen.append(timeout)
        return _Reply()
    rows = _probe(_get)
    assert seen and all(t.read >= 30 for t in seen), seen
    assert all(r["status"] == OK for r in rows), rows


def test_timeout_is_FAIL_and_says_how_long_we_waited():
    def _get(url, timeout=None, headers=None):
        raise httpx.ReadTimeout("The read operation timed out")
    rows = _probe(_get)
    assert rows and all(r["status"] == FAIL for r in rows), rows
    assert all("ждали" in r["message"] for r in rows), rows


# ── токен: пока площадка лежит, о кроне судить нельзя ──

def test_incident_platform_down_is_UNKNOWN_not_cron_stuck():
    # 01.10 04:15: токену 8,3 ч, xarid-api не отвечает.
    st, msg = eimzo_token_verdict(8.3, "auto-vps-eimzo", True, DOWN)
    assert st == UNKNOWN, (st, msg)
    assert "недоступна" in msg and "api.ebirja-auctions" in msg, msg
    assert "stuck" not in msg, msg


def test_platform_down_before_8h_is_UNKNOWN_too():
    assert eimzo_token_verdict(6.0, "auto-vps-eimzo", True, ["api.ebirja-ext"])[0] == UNKNOWN


def test_platform_up_old_token_is_FAIL_pointing_to_cron_log():
    st, msg = eimzo_token_verdict(8.3, "auto-vps-eimzo", True, [])
    assert st == FAIL, (st, msg)
    assert "отвечает" in msg and H.EIMZO_AUTH_LOG in msg, msg
    assert "stuck" not in msg, msg


def test_without_api_probes_does_not_claim_api_is_up():
    st, msg = eimzo_token_verdict(8.3, "auto-vps-eimzo", False, [])
    assert st == FAIL, (st, msg)
    assert "отвечает" not in msg, msg


def test_fresh_token_is_OK_even_while_platform_is_down():
    assert eimzo_token_verdict(1.0, "auto-vps-eimzo", True, DOWN)[0] == OK


def test_5_to_8h_with_platform_up_stays_WARN():
    assert eimzo_token_verdict(6.0, "auto-vps-eimzo", True, [])[0] == WARN


def test_legacy_source_stays_WARN():
    assert eimzo_token_verdict(1.0, "mac", True, [])[0] == WARN


# ── метод целиком: результат API этого прогона доходит до вердикта токена ──

class _Resp(object):
    def __init__(self, data):
        self.data = data


class _Query(object):
    def __init__(self, data):
        self._data = data

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return _Resp(self._data)


class _Client(object):
    def __init__(self, data):
        self._data = data

    def table(self, name):
        return _Query(self._data)


def _eimzo(hc, age_h):
    # Подменяем модуль целиком, а не атрибут: другие тесты сьюта кладут в
    # sys.modules свою заглушку session_store и не всегда её убирают.
    obtained = (datetime.now(timezone.utc) - timedelta(hours=age_h)).isoformat()
    rows = [{"value": json.dumps({"source": "auto-vps-eimzo", "obtained_at": obtained})}]
    stub = types.ModuleType("crawler.auth.session_store")
    stub.session_store = types.SimpleNamespace(_get_client=lambda: _Client(rows))
    key = "crawler.auth.session_store"
    saved = sys.modules.get(key)
    sys.modules[key] = stub
    try:
        hc.check_eimzo_auth()
    finally:
        if saved is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = saved
    return [r for r in hc.results if r["component"] == "eimzo_auth"][-1]


def test_platform_down_keeps_eimzo_out_of_alert_signature():
    hc = HealthCheck()
    hc._add("api.ebirja-ext", WARN, "HTTP 200, но медленно: 10 с")
    for comp in DOWN:
        hc._add(comp, FAIL, "Unreachable (ждали 30 с): The read operation timed out")
    row = _eimzo(hc, 8.3)
    assert row["status"] == UNKNOWN, row
    sig = hc._compute_alert_signature()
    assert "eimzo_auth" not in sig and "api.ebirja-auctions" in sig, sig


def test_platform_up_old_token_still_alerts():
    hc = HealthCheck()
    hc._add("api.ebirja-ext", OK, "HTTP 200")
    row = _eimzo(hc, 8.3)
    assert row["status"] == FAIL, row
    assert "eimzo_auth" in hc._compute_alert_signature()


def test_api_probes_run_before_token_verdict():
    # Вердикт токена читает результаты API из того же прогона.
    import inspect
    src = inspect.getsource(H.main)
    assert src.index("hc.check_api_endpoints()") < src.index("hc.check_eimzo_auth()")


def test_summary_counts_unknown_only_when_present():
    hc = HealthCheck()
    hc._add("x", OK, "fine")
    assert "UNKNOWN" not in hc.summary().splitlines()[2]
    hc._add("eimzo_auth", UNKNOWN, "ebirja недоступна")
    assert hc.summary().splitlines()[2].endswith(", 1 UNKNOWN"), hc.summary()


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
