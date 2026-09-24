"""Сторож баланса OpenRouter в healthcheck (инцидент 23-24.09.2026).

ЧТО СЛУЧИЛОСЬ. Счёт OpenRouter общий у парсинга и SalesBot. Ночные прогоны
SalesBot (~$14) выбрали его до -$0.06. Гейт релевантности при отказе модели
ПРОПУСКАЕТ тендер (notifier._allow, fail-open), поэтому на пустом счёте каждый
вызов получил бы 402, а утренняя волна тендеров ушла бы в канал без фильтра —
в будни гейт отсеивает ~93% из 520-760 кандидатов в сутки. healthcheck в ту ночь
показывал 25 OK / 0 FAIL: баланс он не проверял вовсе.

Эти тесты держат: FAIL наступает ЗАРАНЕЕ (пока запас есть), нечитаемый баланс —
WARN с текстом про среду, а не молчаливый OK, пустой или отвергнутый ключ — FAIL,
и ключ не попадает ни в одно сообщение.

Run: python3 -m crawler.tests.test_openrouter_balance   (exit 1 on any failure)
"""
import sys

import crawler.scripts.healthcheck as H
from crawler.scripts.healthcheck import (
    FAIL, OK, WARN, HealthCheck, OPENROUTER_FAIL_USD, OPENROUTER_WARN_USD,
    openrouter_balance_verdict,
)

SECRET = "sk-or-v1-TESTSECRET-must-never-leak"


# ── чистая функция: пороги ──

def test_incident_numbers_are_FAIL():
    # Ровно те цифры, что были 24.09 в 02:45 UTC.
    st, msg = openrouter_balance_verdict(226.00, 226.06)
    assert st == FAIL, (st, msg)
    assert "пуст" in msg and "402" in msg, msg


def test_zero_is_FAIL():
    st, _ = openrouter_balance_verdict(100.0, 100.0)
    assert st == FAIL


def test_low_but_positive_is_FAIL_in_advance():
    # Главное свойство: тревога ДО нуля, пока гейт ещё работает.
    st, msg = openrouter_balance_verdict(OPENROUTER_FAIL_USD - 0.01, 0.0)
    assert st == FAIL, (st, msg)
    assert "SalesBot" in msg, msg


def test_between_thresholds_is_WARN():
    st, _ = openrouter_balance_verdict(OPENROUTER_FAIL_USD + 0.5, 0.0)
    assert st == WARN


def test_after_topup_is_OK():
    # Замер после пополнения 24.09: куплено 242, потрачено 226.06.
    st, msg = openrouter_balance_verdict(242.00, 226.06)
    assert st == OK, (st, msg)
    assert "15.94" in msg, msg


def test_thresholds_ordered():
    assert 0 < OPENROUTER_FAIL_USD < OPENROUTER_WARN_USD


def test_thresholds_overridable():
    st, _ = openrouter_balance_verdict(20.0, 0.0, fail_below=50.0, warn_below=100.0)
    assert st == FAIL


# ── метод: все ветки отказа, с подменой сети и ключа ──

def _run(key=SECRET, reply=None, raises=None):
    """Прогоняет check_openrouter_balance с подменённым ключом и ответом сети."""
    saved = (H._openrouter_key, H._read_openrouter_credits)
    try:
        H._openrouter_key = lambda: key

        def _fake(k):
            if raises is not None:
                raise raises
            return reply
        H._read_openrouter_credits = _fake
        hc = HealthCheck()
        hc.check_openrouter_balance()
        rows = [r for r in hc.results if r["component"] == "openrouter.balance"]
        assert len(rows) == 1, rows
        return rows[0]
    finally:
        H._openrouter_key, H._read_openrouter_credits = saved


def _body(credits, usage):
    return 200, {"data": {"total_credits": credits, "total_usage": usage}}


def test_method_ok():
    assert _run(reply=_body(242.0, 226.06))["status"] == OK


def test_method_low_balance_FAIL():
    assert _run(reply=_body(226.0, 226.06))["status"] == FAIL


def test_no_key_is_FAIL():
    # Без ключа гейт падает в _allow("no_key") — тот же открытый гейт.
    r = _run(key=None, reply=_body(242.0, 0.0))
    assert r["status"] == FAIL, r


def test_rejected_key_is_FAIL():
    r = _run(reply=(401, None))
    assert r["status"] == FAIL, r
    assert "401" in r["message"]


def test_http_error_is_WARN_not_OK():
    # Проверка, которая не смогла отработать, не имеет права сказать OK.
    r = _run(reply=(500, None))
    assert r["status"] == WARN, r


def test_network_exception_is_WARN_about_environment():
    r = _run(raises=RuntimeError("boom"))
    assert r["status"] == WARN, r
    assert "сеть" in r["message"], r["message"]


def test_malformed_body_is_WARN():
    r = _run(reply=(200, {"data": {"nothing": 1}}))
    assert r["status"] == WARN, r


def test_key_never_leaks_into_messages():
    cases = [dict(reply=_body(242.0, 226.06)), dict(reply=_body(1.0, 5.0)),
             dict(reply=(401, None)), dict(reply=(500, None)),
             dict(raises=RuntimeError(SECRET)), dict(reply=(200, {"data": {}}))]
    for kw in cases:
        r = _run(**kw)
        assert SECRET not in r["message"], (kw, r["message"])
        assert SECRET not in str(r.get("details") or ""), kw


def test_settings_error_does_not_leak_key():
    # pydantic ValidationError кладёт input_value в текст — ключ бы утёк.
    saved = H._openrouter_key
    try:
        def _boom():
            raise ValueError("input_value=%s" % SECRET)
        H._openrouter_key = _boom
        hc = HealthCheck()
        hc.check_openrouter_balance()
        r = [x for x in hc.results if x["component"] == "openrouter.balance"][0]
        assert r["status"] == WARN, r
        assert SECRET not in r["message"], r["message"]
    finally:
        H._openrouter_key = saved


def test_registered_in_healthcheck_run():
    # Сторож, который не вызывается из прогона, не сторожит ничего.
    import inspect
    assert "hc.check_openrouter_balance()" in inspect.getsource(H.main)


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
