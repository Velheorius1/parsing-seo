"""Ввод-вывод сводки по конкурентам: «пора ли» и строка о мониторе площадок (01.10).

Свойства:
  • `--due` ничего не печатает в Telegram и не пишет: exit 10 — рано, 0 — пора;
  • квитанция монитора: нет файла / протухла / не читается — это ошибка В СВОДКЕ
    («монитор не отработал»), а не тишина, и не сегодняшний ноль;
  • новейшая по времени квитанция выигрывает, а не последняя по имени.

Run: python3 -m crawler.tests.test_competitor_wins_weekly_io   (exit 1 on any failure)
"""
import argparse
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.scripts import competitor_wins_weekly as W  # noqa: E402

NOW = datetime(2026, 10, 5, 5, 0, 1, tzinfo=timezone.utc)


def _write(directory, name, payload, age_h=0.0):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(payload if isinstance(payload, str) else json.dumps(payload))
    ts = NOW.timestamp() - age_h * 3600
    os.utime(path, (ts, ts))
    return path


def _delta(new=0):
    return {"new_awards": [{"key": "k%d" % i} for i in range(new)], "changed_awards": [], "bootstrap": False,
            "sources": [{"source_id": "uzex_direct", "status": "complete"},
                        {"source_id": "cooperation_contracts", "label": "Cooperation contracts",
                         "status": "collector_error"}]}


def test_missing_receipts_dir_or_file_is_an_error_not_silence():
    with tempfile.TemporaryDirectory() as d:
        assert "квитанции" in W.read_monitor(d, NOW)["error"]
        assert "квитанции" in W.read_monitor(os.path.join(d, "нет-такой"), NOW)["error"]


def test_fresh_receipt_is_summarised_with_its_failed_sources():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "20261005T040000Z-delta.json", _delta(new=2), age_h=1)
        got = W.read_monitor(d, NOW)
    assert got["new"] == 2 and got["problems"] == ["Cooperation contracts: collector_error"], got
    assert "error" not in got


def test_stale_receipt_is_not_todays_zero():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "20260928T040001Z-delta.json", _delta(), age_h=7 * 24)
        got = W.read_monitor(d, NOW)
    assert "старше" in got["error"], got


def test_newest_receipt_wins_by_mtime_not_by_name():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "z-old-delta.json", _delta(new=9), age_h=5)
        _write(d, "a-fresh-delta.json", _delta(new=1), age_h=1)
        assert W.read_monitor(d, NOW)["new"] == 1


def test_unreadable_receipt_is_reported():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "20261005T040000Z-delta.json", "{не json", age_h=1)
        assert "не читается" in W.read_monitor(d, NOW)["error"]


def _run_due(state):
    import asyncio
    saved = (W._client, W.read_state)
    W._client = lambda: None
    W.read_state = lambda c: state
    try:
        args = argparse.Namespace(due=True, tg=False, force=False, days=0, no_ai=False, dry_run=False,
                                  monitor_receipts="")
        return asyncio.run(W.run(args))
    finally:
        W._client, W.read_state = saved


def test_due_says_not_yet_after_a_fresh_delivery_and_yes_after_three_days():
    now = datetime.now(timezone.utc)
    assert _run_due({"delivered_at": (now - timedelta(days=1)).isoformat()}) == W.EXIT_NOT_DUE == 10
    assert _run_due({"delivered_at": (now - timedelta(days=3, minutes=1)).isoformat()}) == 0
    assert _run_due(None) == 0, "первая доставка — пора"


def test_due_is_a_separate_mode():
    saved = sys.argv
    sys.argv = ["x", "--due", "--tg"]
    try:
        try:
            W.main()
        except SystemExit as exc:
            assert exc.code == 2
            return
        raise AssertionError("--due с --tg должен отказывать")
    finally:
        sys.argv = saved


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
