"""Единый реестр здоровья источника (05.09).

ИЗ ЧЕГО ВЫРОСЛО. Про здоровье источника знали пять сторожей и каждый по-своему:
трекер считал циклы молчания, freshness_watchdog — дни с последней строки,
healthcheck — свежесть geo и «мёртвые за 7 дней», funnel_watchdog — падение
объёма, proxy_health_check — доступность прокси. Списков исключений было три.
Простой прокси 29.08-04.09 показал цену: сигналы шли из четырёх мест, и ни один
не сказал «встали два источника, это 43% потока» — сложить картину было негде.

Здесь закреплено главное свойство реестра: решение человека сильнее замера
(выключенный или объяснённый источник не «сломан»), а тяжёлый молчун отделён
от лёгкого.

Run: python3 -m crawler.tests.test_source_registry   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta, timezone

from crawler.tests._stubs import install_settings_stub, install_stub

install_settings_stub()

from crawler.core import source_health as SH  # noqa: E402


def _rec(**kw):
    base = {"id": "x", "name": "Источник", "enabled": True, "external": False,
            "excused": False,
            "muted_push": False, "alerts": 0, "share_pct": 0.0, "rows": 100,
            "last_collected": "2026-09-05T00:00:00", "silent_hours": 1.0,
            "zeros": 0, "alerted": False, "rhythm_hours": None,
            "threshold_hours": None}
    base.update(kw)
    return base


# ── вердикт ─────────────────────────────────────────────────────────────────

def test_fresh_source_is_ok():
    assert SH._verdict(_rec(silent_hours=2.0)) == SH.VERDICT_OK


def test_heavy_stale_is_separated_from_light():
    heavy = _rec(silent_hours=100.0, share_pct=26.0)
    light = _rec(silent_hours=100.0, share_pct=0.4)
    assert SH._verdict(heavy) == SH.VERDICT_HEAVY_STALE
    assert SH._verdict(light) == SH.VERDICT_SILENT


def test_human_decision_beats_the_measurement():
    """Выключенный, объяснённый и заглушённый по пушам молчат по договорённости."""
    for flags in ({"enabled": False}, {"excused": True}, {"muted_push": True}):
        rec = _rec(silent_hours=500.0, share_pct=30.0, **flags)
        assert SH._verdict(rec) == SH.VERDICT_SILENT_EXPECTED, flags


def test_source_without_rows_is_never_not_broken():
    assert SH._verdict(_rec(rows=0, silent_hours=None)) == SH.VERDICT_NEVER


def test_own_rhythm_wins_over_the_default_threshold():
    """Частый источник ловится раньше 24ч, редкий не считается сломанным."""
    fast = _rec(silent_hours=8.0, rhythm_hours=2.0, threshold_hours=6.0, share_pct=9.0)
    slow = _rec(silent_hours=100.0, rhythm_hours=168.0, threshold_hours=336.0, share_pct=9.0)
    assert SH._verdict(fast) == SH.VERDICT_HEAVY_STALE
    assert SH._verdict(slow) == SH.VERDICT_OK


# ── сборка реестра ──────────────────────────────────────────────────────────

def _build(tmpdir, cfg_text, fresh_rows, weights, tracker):
    import os
    path = os.path.join(tmpdir, "sources.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(cfg_text)
    orig_w, orig_f, orig_m = SH.source_weights, SH._freshness_rows, SH._sources_we_never_push
    SH.source_weights = lambda **k: {"weights": weights, "total": 10, "heavy": []}
    SH._freshness_rows = lambda: fresh_rows
    SH._sources_we_never_push = lambda: set()
    store = types.SimpleNamespace(get_setting=lambda k: {"sources": tracker},
                                  set_setting=lambda k, v: True)
    mod = install_stub("crawler.auth.session_store", session_store=store)
    prev = getattr(mod, "session_store", None)
    mod.session_store = store
    try:
        return SH.build_registry(path)
    finally:
        SH.source_weights, SH._freshness_rows, SH._sources_we_never_push = orig_w, orig_f, orig_m
        if prev is not None:
            mod.session_store = prev


def test_registry_joins_config_weight_freshness_and_tracker(tmp_path):
    now = datetime.now(timezone.utc)
    reg = _build(
        str(tmp_path),
        "sources:\n  - id: a\n    name: Альфа\n    enabled: true\n"
        "  - id: b\n    name: Бета\n    enabled: false\n",
        [{"source": "Альфа", "cnt": 500,
          "last_collected": (now - timedelta(hours=3)).isoformat()}],
        {"Альфа": {"alerts": 9, "pct": 90.0}},
        {"a": {"consecutive_zeros": 1, "alerted": False, "data_gaps": [2, 2, 2]}},
    )
    by_id = {r["id"]: r for r in reg["sources"]}
    assert by_id["a"]["share_pct"] == 90.0
    assert by_id["a"]["rows"] == 500
    assert 2.5 < by_id["a"]["silent_hours"] < 3.5
    assert by_id["a"]["rhythm_hours"] == 2
    from crawler.core.zero_result_tracker import MIN_SILENCE_HOURS
    assert by_id["a"]["threshold_hours"] == MIN_SILENCE_HOURS, \
        "порог частого источника должен упереться в пол, а не считаться от нуля"
    assert by_id["b"]["verdict"] == SH.VERDICT_SILENT_EXPECTED, "выключенный не «сломан»"
    assert reg["alerts_total"] == 10


def test_sources_collected_outside_the_config_are_included(tmp_path):
    """Cooperation.uz Лоты собирается отдельным скриптом под прокси и в
    sources.yaml не значится. Реестр по одному конфигу терял источник №2 по
    алертам — 26% потока (поймано первым прогоном на живых данных 05.09)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    reg = _build(
        str(tmp_path),
        "sources:\n  - id: a\n    name: Альфа\n",
        [{"source": "Альфа", "cnt": 10, "last_collected": now.isoformat()},
         {"source": "Cooperation.uz Лоты", "cnt": 900,
          "last_collected": (now - timedelta(hours=100)).isoformat()},
         {"source": "Древний источник", "cnt": 5,
          "last_collected": (now - timedelta(days=400)).isoformat()}],
        {"Cooperation.uz Лоты": {"alerts": 265, "pct": 26.1}}, {})
    by_name = {r["name"]: r for r in reg["sources"]}
    assert "Cooperation.uz Лоты" in by_name, "внешний источник снова потерян"
    coop = by_name["Cooperation.uz Лоты"]
    assert coop["external"] is True and coop["share_pct"] == 26.1
    assert coop["verdict"] == SH.VERDICT_HEAVY_STALE, "тяжёлый внешний молчун не помечен"
    assert "Древний источник" not in by_name, "мусор из старых эпох попал в реестр"


def test_registry_is_sorted_by_weight(tmp_path):
    reg = _build(
        str(tmp_path),
        "sources:\n  - id: small\n    name: Малый\n  - id: big\n    name: Большой\n",
        [], {"Большой": {"alerts": 9, "pct": 90.0}, "Малый": {"alerts": 1, "pct": 10.0}}, {})
    assert [r["id"] for r in reg["sources"]] == ["big", "small"]


def test_broken_config_gives_empty_registry_not_a_crash(tmp_path):
    orig_w, orig_f = SH.source_weights, SH._freshness_rows
    SH.source_weights = lambda **k: {"weights": {}, "total": 0, "heavy": []}
    SH._freshness_rows = lambda: []
    try:
        reg = SH.build_registry(str(tmp_path) + "/нет-такого.yaml")
        assert reg["sources"] == []
    finally:
        SH.source_weights, SH._freshness_rows = orig_w, orig_f


# ── вывод для человека ──────────────────────────────────────────────────────

def test_digest_leads_with_breakage_and_its_share():
    from crawler.scripts.source_registry import render_digest
    reg = {"alerts_total": 1014, "generated_at": "", "sources": [
        _rec(id="c", name="Cooperation.uz Лоты", silent_hours=96.0, share_pct=26.1,
             verdict=SH.VERDICT_HEAVY_STALE),
        _rec(id="p", name="UZEX Предквалификации", silent_hours=80.0, share_pct=20.8,
             verdict=SH.VERDICT_HEAVY_STALE),
        _rec(id="s", name="Мелкий", silent_hours=200.0, verdict=SH.VERDICT_SILENT),
        _rec(id="e", name="Выключенный", verdict=SH.VERDICT_SILENT_EXPECTED),
        _rec(id="o", name="Живой", verdict=SH.VERDICT_OK),
    ]}
    out = render_digest(reg)
    assert out.index("Поломки (2)") < out.index("Молчат без объяснения (1)")
    assert "47% потока" in out, out
    assert "Cooperation.uz Лоты — молчит 4д" in out
    assert "Молчат по договорённости: 1" in out
    assert "Свежие: 1" in out


def test_digest_fits_telegram_limit():
    from crawler.scripts.source_registry import render_digest
    reg = {"alerts_total": 1, "generated_at": "", "sources": [
        _rec(id=str(i), name="Источник с довольно длинным именем %03d" % i,
             silent_hours=300.0, verdict=SH.VERDICT_SILENT) for i in range(200)]}
    out = render_digest(reg)
    assert len(out) <= 4096
    assert "и ещё" in out


def test_digest_says_it_is_a_dashboard_not_an_alarm():
    """Иначе витрина читается как тревога и обесценивает настоящие."""
    from crawler.scripts.source_registry import render_digest
    out = render_digest({"alerts_total": 0, "generated_at": "", "sources": []})
    assert "витрина, а не тревога" in out


if __name__ == "__main__":
    import tempfile

    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            if "tmp_path" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    fn(d)
            else:
                fn()
            print("PASS", name)
        except Exception as exc:
            print("FAIL", name, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
