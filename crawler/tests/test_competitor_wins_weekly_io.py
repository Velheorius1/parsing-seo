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

from crawler.core import competitor_wins as CW  # noqa: E402
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


def test_receipt_is_judged_by_the_monitors_own_reported_statuses():
    """Ревью #54: covered_by_digest и complete_name_only — отчитались; partial_identity
    монитор сам не считает полным — значит, и сводка не молчит."""
    delta = {"new_awards": [], "telegram_delivered": True,
             "sources": [{"source_id": "etender_deals", "status": "covered_by_digest"},
                         {"source_id": "ebirja_auction", "status": "complete_name_only"},
                         {"source_id": "cooperation_contracts", "label": "Cooperation contracts",
                          "status": "partial_identity"}]}
    with tempfile.TemporaryDirectory() as d:
        _write(d, "20261005T040000Z-delta.json", delta, age_h=1)
        got = W.read_monitor(d, NOW)
    assert got["problems"] == ["Cooperation contracts: partial_identity"], got
    assert got["delivered"] is True


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


# ── build_report: два фида, повторы смены формата, retry, сшивка лотов ─────────

class _Q(object):
    """Минимальный PostgREST-запрос: eq / in_ / lt по строкам таблицы в памяти."""

    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        return _Q([r for r in self.rows if r.get(col) == val])

    def in_(self, col, vals):
        return _Q([r for r in self.rows if r.get(col) in set(vals)])

    def lt(self, col, val):
        return _Q([r for r in self.rows if r.get(col) < val])

    def execute(self):
        return types.SimpleNamespace(data=list(self.rows), count=len(self.rows))


class _Client(object):
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Q(self.rows)


def _civil(ext, created, inn="123456789", rid=None):
    return {"id": rid or ext, "source": "UZEX Результаты", "external_id": ext, "created_at": created,
            "title": "Jurnal chop etish", "organization": "MUDOFAA VAZIRLIGI", "price": 60e6,
            "winning_price": 50e6, "currency": "UZS", "winner": "OLTIN NASHR (ИНН %s)" % inn,
            "result_date": "2026-10-03", "message_type": "result", "status": "completed"}


def test_seen_before_catches_a_result_rewritten_under_a_new_id_format():
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    db = [_civil("result-26120000010070", "2026-08-20T00:00:00+00:00")]
    rows = [_civil("result-26120500010070", "2026-10-03T00:00:00+00:00"),
            _civil("result-26120500010071", "2026-10-03T00:00:00+00:00")]
    assert W.civil_seen_before(_Client(db), rows, start) == {CW.civil_norm_key("result-26120500010070")}
    late = [_civil("result-26120000010071", "2026-10-03T00:00:00+00:00")]
    assert W.civil_seen_before(_Client(late), rows, start) == set(), "двойник внутри окна — это не «знали раньше»"


def test_our_lot_from_before_the_switch_matches_a_result_after_it():
    lot = {"id": "L", "source": "ETender Отбор (ВМК-69)", "external_id": "26110000017697", "alert_seq": 5}
    got = W.fetch_civil_lot_rows(_Client([lot]), ["26110500017697"])
    assert got.get(CW.civil_norm_key("26110500017697")) == [lot], got


async def _all_profile(entries, use_ai, budget):
    for e in entries:
        if e.get("profile") is None:
            e["profile"] = True


def _build(window, retry_ids=(), **stubs):
    """build_report без базы и сети: фиды и суд — стабы, реестр — настоящий файл."""
    import asyncio
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    base = {"_client": lambda: None,
            "fetch_deals": lambda c, s, e: [],
            "fetch_civil": lambda c, s, e: [dict(r) for r in window],
            "civil_seen_before": lambda c, rows, s: set(),
            "fetch_deals_by_id": lambda c, ids: [],
            "count_deals": lambda c, s, e, source=None: 3,
            "fetch_lot_rows": lambda c, urls: {},
            "fetch_civil_lot_rows": lambda c, keys: {},
            "fetch_human_labels": lambda c, seqs: {},
            "fetch_our_actions": lambda c, seqs: {},
            "judge": _all_profile,
            "keyword_hit_fn": lambda: (lambda text: False)}
    base.update(stubs)
    saved = {n: getattr(W, n) for n in base}
    for n, v in base.items():
        setattr(W, n, v)
    try:
        return asyncio.run(W.build_report(start, start + timedelta(days=3), False, list(retry_ids), use_ai=False))
    finally:
        for n, v in saved.items():
            setattr(W, n, v)


def test_build_report_merges_both_feeds_and_drops_repeats():
    window = [_civil("result-26120500010069", "2026-10-03T00:00:00+00:00"),
              _civil("result-26120000010069", "2026-10-03T01:00:00+00:00"),   # двойник в том же окне
              _civil("result-26120500010070", "2026-10-03T02:00:00+00:00")]   # старый итог под новым id
    retried = _civil("result-26120500010080", "2026-09-29T00:00:00+00:00", inn="987654321", rid="retry-1")
    # наш лот собран до смены формата: «00», итог пришёл с «05»
    our_lot = {"id": "L", "source": "ETender Отбор (ВМК-69)", "external_id": "26120000010069", "alert_seq": 7,
               "created_at": "2026-09-20T00:00:00+00:00", "deadline": "2026-09-27T00:00:00+00:00",
               "title": "Jurnal chop etish"}
    report, _registry, undecided = _build(
        window, ["retry-1"],
        civil_seen_before=lambda c, rows, s: {CW.civil_norm_key("result-26120500010070")},
        fetch_deals_by_id=lambda c, ids: [retried] if ids == ["retry-1"] else [],
        fetch_civil_lot_rows=lambda c, keys: {CW.civil_norm_key("26120000010069"): [our_lot]})
    keys = sorted(it["win"]["lot_key"] for it in report["items"])
    assert keys == ["26120500010069", "26120500010080"], keys
    assert all(it["win"]["feed"] == CW.FEED_CIVIL for it in report["items"])
    assert {it["win"]["winner_inn"] for it in report["items"]} == {"123456789", "987654321"}, \
        "retry-строка ВМК-69 должна разбираться как итог, а не как сделка"
    cov = report["coverage"]
    assert cov["civil"] == 3 and cov["civil_repeats"] == 2 and cov["retried"] == 1, cov
    assert report["items"][0]["win"]["source_url"].startswith("https://etender.uzex.uz/civil-detail/")
    assert undecided == []
    status = {it["win"]["lot_key"]: it["status"] for it in report["items"]}
    assert status["26120500010069"] == CW.STATUS_ALERTED, "лот «00» и итог «05» — одна процедура: %r" % status
    assert status["26120500010080"] == CW.STATUS_NOT_COLLECTED, status


OFSET_FAYZ, OFSET_SURXON = "205888800", "300579386"


def test_excluded_firms_are_not_hidden_printers():
    """02.10: OFSET-FAYZ (FPV-очки, связь) шёл в «спрятанные» по слову OFSET в имени,
    хотя в реестре записан как «не типография»."""
    window = [dict(_civil("result-26120500010090", "2026-10-03T00:00:00+00:00"),
                   winner="OFSET-FAYZ МЧЖ (ИНН %s)" % OFSET_FAYZ, title="алоқа воситалари"),
              dict(_civil("result-26120500010091", "2026-10-03T00:00:00+00:00"),
                   winner="OFSET-SURXON (ИНН %s)" % OFSET_SURXON, title="Poligrafiya mahsulotlari")]

    async def none_ours(entries, use_ai, budget):
        for e in entries:
            e["profile"] = False

    report, _registry, _undecided = _build(window, judge=none_ours,
                                           keyword_hit_fn=lambda: (lambda text: "ofset" in text.lower()))
    assert [it["win"]["winner_inn"] for it in report["printers"]] == [OFSET_SURXON], report["printers"]


def test_excluded_inns_come_from_the_registry_and_stay_off_the_list():
    from crawler.core.competitor_audit import load_registry, registry_inns
    excluded = W.excluded_inns()
    assert OFSET_FAYZ in excluded and len(excluded) >= 10, excluded
    assert not excluded & set(registry_inns(load_registry())), "фирма и в списке, и в исключённых"



# ── «спрятанные»: предмет судит AI (05.10) ────────────────────────────────────

def _hidden(price, title):
    return {"win": {"won_price": price, "feed": CW.FEED_DEALS}, "deal_row": {},
            "lot_row": {"title": title}, "status": CW.STATUS_NOT_COLLECTED}


def _screen(cands, verdicts, cap=60, use_ai=True):
    import asyncio
    asked = []

    async def ask(row):
        asked.append(row["title"])
        return verdicts[row["title"]]
    budget = W._Budget(cap)
    kept, dropped, unchecked = asyncio.run(W.screen_hidden(cands, use_ai, budget, ask=ask))
    return [e["lot_row"]["title"] for e in kept], dropped, unchecked, asked, budget


def test_hidden_with_foreign_subject_is_dropped_ours_and_ai_failures_stay():
    cands = [_hidden(60e6, "стенд"), _hidden(808e6, "кондиционер"), _hidden(70e6, "связь")]
    kept, dropped, unchecked, asked, budget = _screen(
        cands, {"стенд": True, "кондиционер": False, "связь": None})
    assert kept == ["стенд", "связь"], "порядок вывода прежний, сбой AI не прячет лот"
    assert (dropped, unchecked) == (1, 1)
    assert asked[0] == "кондиционер", "дорогие первыми — бюджет съедает мелочь"
    assert budget.used == 3 and budget.errors == 1


def test_hidden_without_budget_or_without_ai_is_kept_unchecked():
    cands = [_hidden(60e6, "a"), _hidden(70e6, "b")]
    kept, dropped, unchecked, asked, _ = _screen(cands, {"a": False, "b": False}, cap=1)
    assert kept == ["a"] and dropped == 1 and unchecked == 1 and asked == ["b"]
    kept, dropped, unchecked, asked, _ = _screen(cands, {"a": False, "b": False}, use_ai=False)
    assert kept == ["a", "b"] and (dropped, unchecked) == (0, 0) and asked == []


def test_coverage_line_names_what_the_hidden_filter_did():
    cov = {"deals": 1, "hidden_ai_dropped": 2, "hidden_unchecked": 1}
    text = CW.build_message({"items": [], "printers": [], "coverage": cov,
                             "start": NOW - timedelta(days=3), "end": NOW})
    assert "из «спрятанных» AI убрал чужой предмет 2" in text, text
    assert "«спрятанных» без проверки AI 1" in text, text

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
