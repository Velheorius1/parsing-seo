"""Повтор тревоги растягивается и несёт возраст поломки (05.09).

ИЗ ЧЕГО ВЫРОСЛО. Инцидент 29.08-04.09: резидентный прокси упёрся в 402, и
healthcheck ровно каждые четыре часа слал ОДИН И ТОТ ЖЕ текст
«token.cooperation EXPIRED». Около 21 одинакового сообщения за 3,5 дня — чем
дольше держалась поломка, тем меньше на неё смотрели. Повтор без новой
информации не настойчивость, а шум, который учит игнорировать канал.

Здесь закреплено: окно повтора растёт 4 → 8 → 16 → 24 часа, текст повторной
тревоги несёт возраст, смена набора поломок начинает отсчёт заново, а
выздоровление чистит состояние.

Run: python3 -m crawler.tests.test_alert_escalation   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta, timezone

from crawler.tests._stubs import install_settings_stub, install_stub

install_settings_stub()

import crawler.scripts.healthcheck as H  # noqa: E402


class _Store(object):
    def __init__(self, initial=None):
        self.saved = dict(initial) if initial else {}

    def get_setting(self, _key):
        return dict(self.saved)

    def set_setting(self, _key, value):
        self.saved = dict(value)
        return True


def _hc(prior=None, fails=("token.cooperation",)):
    hc = H.HealthCheck()
    hc.settings = types.SimpleNamespace(telegram_bot_token="t", telegram_alert_chat_id="c")
    for comp in fails:
        hc._add(comp, H.FAIL, "EXPIRED")
    sent = []
    hc._send_alert_body = lambda body: sent.append(body)
    store = _Store(prior)
    install_stub("crawler.auth.session_store", session_store=store)
    import crawler.auth.session_store as ss
    prev = ss.session_store
    ss.session_store = store
    return hc, sent, store, (ss, prev)


def _restore(ctx):
    ss, prev = ctx
    ss.session_store = prev


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def test_first_alert_is_sent_without_an_age_line():
    hc, sent, store, ctx = _hc()
    try:
        assert hc.handle_alert_on_fail() == "sent"
        assert len(sent) == 1
        assert "держится" not in sent[0], "первая тревога не может быть «давней»"
        assert store.saved["repeats"] == 1 and store.saved["first_at"]
    finally:
        _restore(ctx)


def test_repeat_inside_the_window_is_suppressed():
    hc, sent, store, ctx = _hc()
    try:
        sig = hc._compute_alert_signature(suppress_cascade=True)
        store.saved = {"signature": sig, "alerted_at": _ago(1), "first_at": _ago(1),
                       "repeats": 1}
        assert hc.handle_alert_on_fail() == "suppressed"
        assert sent == []
    finally:
        _restore(ctx)


def test_window_doubles_with_each_repeat_up_to_a_day():
    hc, sent, store, ctx = _hc()
    try:
        sig = hc._compute_alert_signature(suppress_cascade=True)
        # после второго повтора окно 8ч: через 5 часов ещё рано
        store.saved = {"signature": sig, "alerted_at": _ago(5), "first_at": _ago(30),
                       "repeats": 2}
        assert hc.handle_alert_on_fail() == "suppressed", "окно не выросло"
        # через 9 часов — пора
        store.saved = {"signature": sig, "alerted_at": _ago(9), "first_at": _ago(40),
                       "repeats": 2}
        assert hc.handle_alert_on_fail() == "sent"
        assert store.saved["repeats"] == 3
    finally:
        _restore(ctx)


def test_backoff_is_capped_at_a_day():
    hc, sent, store, ctx = _hc()
    try:
        sig = hc._compute_alert_signature(suppress_cascade=True)
        store.saved = {"signature": sig, "alerted_at": _ago(25), "first_at": _ago(400),
                       "repeats": 99}
        assert hc.handle_alert_on_fail() == "sent", "потолок окна не сработал — тишина навсегда"
    finally:
        _restore(ctx)


def test_repeat_carries_the_age_of_the_breakage():
    hc, sent, store, ctx = _hc()
    try:
        sig = hc._compute_alert_signature(suppress_cascade=True)
        store.saved = {"signature": sig, "alerted_at": _ago(30), "first_at": _ago(84),
                       "repeats": 3}
        hc.handle_alert_on_fail()
        assert "держится 3 дн подряд" in sent[0], sent[0]
        assert store.saved["first_at"], "возраст поломки потерян при повторе"
    finally:
        _restore(ctx)


def test_age_is_in_hours_below_two_days():
    now = datetime.now(timezone.utc)
    # допуск в час: между _ago() и замером проходят микросекунды, и int() режет вниз
    assert H.HealthCheck._alert_age_line(_ago(30), now) in (
        "⏳ держится 30 ч подряд", "⏳ держится 29 ч подряд")
    assert H.HealthCheck._alert_age_line(_ago(0.5), now) == ""
    assert H.HealthCheck._alert_age_line("мусор", now) == ""
    assert H.HealthCheck._alert_age_line(None, now) == ""


def test_new_signature_restarts_the_count():
    """Другая поломка — другая история: она не должна наследовать растянутое
    окно предыдущей и молчать сутки."""
    hc, sent, store, ctx = _hc()
    try:
        store.saved = {"signature": "совсем-другая", "alerted_at": _ago(1),
                       "first_at": _ago(200), "repeats": 9}
        assert hc.handle_alert_on_fail() == "sent"
        assert store.saved["repeats"] == 1
        assert "держится" not in sent[0]
    finally:
        _restore(ctx)


def test_recovery_clears_the_state():
    hc = H.HealthCheck()
    hc.settings = types.SimpleNamespace(telegram_bot_token="t", telegram_alert_chat_id="c")
    hc._add("supabase", H.OK, "fine")
    sent = []
    hc._send_alert_body = lambda body: sent.append(body)
    store = _Store({"signature": "было", "alerted_at": _ago(1), "repeats": 4})
    import crawler.auth.session_store as ss
    prev = ss.session_store
    ss.session_store = store
    try:
        assert hc.handle_alert_on_fail() == "recovery"
        assert store.saved == {}
        assert "RECOVERY" in sent[0]
    finally:
        ss.session_store = prev


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
