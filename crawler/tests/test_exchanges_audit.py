"""Ночной аудит площадок не врёт об отправке и не шумит ложными FAIL (10.10.2026).

ЧТО БЫЛО. 172 ночи подряд в логе стояло «Report sent to Telegram», хотя строкой
выше было «TG token/chat not configured, skipping send»: токен искался в
переменных окружения, которых у cron нет, а «отправлено» писалось без условия.
Отчёт при этом был бы мусором: список источников жил руками, из 23 FAIL почти
все — выведенные ленты Cooperation и старые имена («SQB», «MOBIUZ», «TrustBank»),
под которыми в базе давно ничего нет, хотя сами источники собирают.

Здесь держится: список строится из sources.yaml, без Telegram и выключенных,
с лентами скриптов; объяснённое молчание — не FAIL; «отправлено» — только при
ответе Telegram 200, иначе код 2 и состояние не сохраняется; с --only-fail
отчёт уходит лишь при изменении набора FAIL.

Run: python3 -m crawler.tests.test_exchanges_audit   (exit 1 on any failure)
"""
import os
import sys
import tempfile
import types

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.scripts.exchanges_audit as A  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = yaml.safe_load(open(os.path.join(ROOT, "crawler", "config", "sources.yaml")))["sources"]
NAMES = {s["name"]: s for s in RAW}


def _names():
    return [s["name"] for s in A.build_sources()]


def test_list_comes_from_config_not_stale_names():
    names = _names()
    for stale in ("SQB", "MOBIUZ", "TrustBank", "АнорБанк", "Uz-airways", "Tashkent Steel"):
        assert stale not in names, stale
    assert "Tashkent Steel (ТМЗ)" in names


def test_every_name_is_a_real_source_or_script_feed():
    for n in _names():
        assert n in NAMES or n in A.SCRIPT_FEEDS, n


def test_no_telegram_and_no_disabled_sources():
    for n in _names():
        s = NAMES.get(n)
        if s is None:
            continue
        assert s.get("adapter") != "telegram", n
        assert s.get("enabled", True) or n in A.SCRIPT_FEEDS, n


def test_script_fed_feeds_are_audited():
    names = _names()
    for feed in ("Cooperation.uz Лоты", "UZEX Результаты"):
        assert feed in names, feed


def _audit_with(name, collection_status, excuse):
    old = (A.check_http, A.check_collection, A.check_fields, A.check_alerts_ratio,
           A.check_dups, A.check_stale_deadlines, A.silence_excuse)
    skip = lambda c, n: {"status": "skip", "msg": ""}  # noqa: E731
    A.check_http = lambda url: ("skip", "")
    A.check_collection = lambda c, n: {"status": collection_status, "msg": "DEAD"}
    A.check_fields = A.check_alerts_ratio = A.check_dups = A.check_stale_deadlines = skip
    A.silence_excuse = lambda n: excuse
    try:
        return A.audit_source(None, {"name": name})
    finally:
        (A.check_http, A.check_collection, A.check_fields, A.check_alerts_ratio,
         A.check_dups, A.check_stale_deadlines, A.silence_excuse) = old


def test_explained_silence_is_not_fail():
    r = _audit_with("Cooperation.uz Пакеты", A.FAIL, {"category": A.EXCUSE_RETIRED, "reason": "выведен"})
    assert A.overall_status(r) != A.FAIL


def test_unexplained_silence_stays_fail():
    r = _audit_with("Ипотека-банк", A.FAIL, None)
    assert A.overall_status(r) == A.FAIL


def test_alerts_off_does_not_excuse_dead_collection():
    # «Алерты выключены» — про алерты, не про сбор: мёртвый сбор всё равно FAIL.
    r = _audit_with("XT-Xarid э-магазин", A.FAIL, {"category": "alerts_off", "reason": "витрина"})
    assert A.overall_status(r) == A.FAIL


def test_should_send_only_on_change():
    assert A.should_send(["a"], None) is True
    assert A.should_send([], None) is False
    assert A.should_send(["a", "b"], ["b", "a"]) is False
    assert A.should_send(["a", "c"], ["a"]) is True
    assert A.should_send([], ["a"]) is True  # «всё починилось» — тоже новость


def _run_main(results, send_ok, prev=None, argv=("--telegram", "--only-fail")):
    d = tempfile.mkdtemp(prefix="audit-")
    state = os.path.join(d, "sent.json")
    if prev is not None:
        A._save_sent(prev, path=state)
    sent = []
    old = (A._get_supabase, A.build_sources, A.audit_source, A.send_telegram,
           A._SENT_STATE, sys.argv)
    A._get_supabase = lambda: None
    A.build_sources = lambda: [{"name": r["name"]} for r in results]
    by_name = {r["name"]: r for r in results}
    A.audit_source = lambda c, s: by_name[s["name"]]
    A.send_telegram = lambda text: (sent.append(text), send_ok)[1]
    A._SENT_STATE = state
    sys.argv = ["exchanges_audit"] + list(argv)
    try:
        # _load_sent/_save_sent берут путь по умолчанию на момент определения —
        # подменяем вызовы, чтобы они смотрели во временный файл.
        orig_load, orig_save = A._load_sent, A._save_sent
        A._load_sent = lambda path=state: orig_load(path)
        A._save_sent = lambda names, path=state: orig_save(names, path)
        try:
            code = A.main()
        finally:
            A._load_sent, A._save_sent = orig_load, orig_save
        return code, sent, A._load_sent(state)
    finally:
        (A._get_supabase, A.build_sources, A.audit_source, A.send_telegram,
         A._SENT_STATE, sys.argv) = old


def _res(name, status):
    return {"name": name, "collection": {"status": status, "msg": "DEAD"}}


def test_failed_delivery_is_an_error_and_not_remembered():
    code, sent, saved = _run_main([_res("Ипотека-банк", A.FAIL)], send_ok=False)
    assert sent, "отчёт даже не пытались отправить"
    assert code == 2
    assert saved is None, "недоставленный отчёт записан как отправленный"


def test_delivered_report_is_remembered():
    code, sent, saved = _run_main([_res("Ипотека-банк", A.FAIL)], send_ok=True)
    assert sent and saved == ["Ипотека-банк"]


def test_same_fail_set_is_not_resent():
    code, sent, saved = _run_main([_res("Ипотека-банк", A.FAIL)], send_ok=True, prev=["Ипотека-банк"])
    assert sent == []


def test_report_marks_new_and_fixed():
    _, sent, _ = _run_main([_res("UNGM", A.FAIL), _res("Ипотека-банк", A.OK)], send_ok=True,
                           prev=["Ипотека-банк"])
    assert sent and "Новые FAIL: UNGM" in sent[0] and "Починились: Ипотека-банк" in sent[0], sent


def test_send_telegram_needs_token():
    old = A.settings
    A.settings = types.SimpleNamespace(telegram_bot_token="", telegram_alert_chat_id="")
    env = (os.environ.pop("TELEGRAM_BOT_TOKEN", None), os.environ.pop("TELEGRAM_ALERT_CHAT_ID", None))
    try:
        assert A.send_telegram("x") is False
    finally:
        A.settings = old
        if env[0] is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = env[0]
        if env[1] is not None:
            os.environ["TELEGRAM_ALERT_CHAT_ID"] = env[1]


def test_send_telegram_checks_every_response():
    old_settings, old_post = A.settings, A._tg_post
    A.settings = types.SimpleNamespace(telegram_bot_token="t", telegram_alert_chat_id="c")
    calls = []
    try:
        A._tg_post = lambda token, payload: (calls.append(payload), False)[1]
        assert A.send_telegram("отчёт") is False
        assert len(calls) == 2  # Markdown и повтор простым текстом
        calls[:] = []
        A._tg_post = lambda token, payload: (calls.append(payload), True)[1]
        assert A.send_telegram("отчёт") is True and len(calls) == 1
    finally:
        A.settings, A._tg_post = old_settings, old_post


class _Query(object):
    """Пишет, по каким колонкам фильтровали, и отдаёт пустой ответ."""

    def __init__(self, log):
        self.log = log
        self.not_ = self

    def __getattr__(self, name):
        def call(*a, **kw):
            if name in ("gte", "lt", "eq", "is_", "gt", "neq", "ilike"):
                self.log.append((name, a[0] if a else None))
            return self
        return call

    def execute(self):
        return types.SimpleNamespace(data=[], count=0)


class _Client(object):
    def __init__(self):
        self.log = []

    def table(self, name):
        return _Query(self.log)


def test_dups_and_stale_count_new_rows_not_recollected():
    # collected_at переписывается при каждом сборе: майские алерты SQB читались
    # как «шесть дублей за сутки». Сутки считаем по created_at.
    for fn in (A.check_dups, A.check_stale_deadlines):
        c = _Client()
        fn(c, "Cooperation.uz Закупочные планы (filtered)")
        cols = [col for op, col in c.log if op == "gte"]
        assert cols == ["created_at"], (fn.__name__, cols)


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
