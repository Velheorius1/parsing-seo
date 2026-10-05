"""Источники со своим скриптом: молчание — поломка, а не решение (05.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. «Ebirja Договоры» пишет не runner, а fetch_ebirja_contracts.py,
поэтому в sources.yaml они стоят `enabled: false`. Для реестра здоровья это
значило «выключен — молчит по решению». 21.09 скрипт начал падать на записи
(PGRST204, лишняя колонка winner_name) и две недели писал «Fetched: 63,
Upserted: 0». Сторожа видели «выключен»; Отбор, которого в конфиге не было,
реестр пометил «silent» — но реестр витрина без звука, и тревоги не было.

Здесь закреплено: источник с `collected_by` молчит дольше трёх прогонов скрипта —
FAIL с именем скрипта; штатный разрыв между прогонами (12 ч) — не тревога;
проверка зависит от Supabase (её падение не должно рождать ложный FAIL).

Run: python3 -m crawler.tests.test_scripted_sources   (exit 1 on any failure)
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.scripts.healthcheck as H  # noqa: E402

_YAML = """sources:
  - id: ebirja-contracts-shop
    name: "Ebirja Договоры (Э-магазин)"
    adapter: spa
    enabled: false
    collected_by: fetch_ebirja_contracts
    url: "https://x"
    id_prefix: "ebirja-ctr"
  - id: ebirja-contracts-selection
    name: "Ebirja Договоры (Отбор)"
    adapter: spa
    enabled: false
    collected_by: fetch_ebirja_contracts
    url: "https://x"
    id_prefix: "ebirja-ctr"
  - id: runner-source
    name: "Обычный"
    adapter: api
    url: "https://x"
    id_prefix: "x"
"""


class _Res(object):
    def __init__(self, data):
        self.data = data


class _Table(object):
    def __init__(self, last):
        self._last, self._src = last, None

    def select(self, *a, **k):
        return self

    def eq(self, _col, val):
        self._src = val
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        val = self._last.get(self._src)
        return _Res([{"collected_at": val}] if val else [])


class _Client(object):
    def __init__(self, last):
        self._last = last

    def table(self, _name):
        return _Table(self._last)


def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _run(last, yaml_text=_YAML):
    hc = H.HealthCheck()
    hc.client = _Client(last)
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml_text)
        hc.check_scripted_sources(config_path=path)
    finally:
        os.unlink(path)
    return [r for r in hc.results if r["component"] == "sources.scripted"][0]


def test_two_weeks_of_silence_is_a_fail_naming_the_script():
    r = _run({"Ebirja Договоры (Э-магазин)": _iso(14 * 24), "Ebirja Договоры (Отбор)": _iso(3)})
    assert r["status"] == "fail", r
    assert "Ebirja Договоры (Э-магазин) молчит 336ч — пишет fetch_ebirja_contracts" in r["message"], r
    assert "Отбор" not in r["message"], "свежий источник в тревогу не попадает"


def test_normal_gap_between_script_runs_is_ok():
    """Скрипт идёт в 07:30 и 19:30 — 12 часов тишины это норма."""
    r = _run({"Ebirja Договоры (Э-магазин)": _iso(12), "Ebirja Договоры (Отбор)": _iso(13)})
    assert r["status"] == "ok", r
    assert "2 источников" in r["message"], "обычный runner-источник сюда не входит"


def test_source_without_rows_is_stale():
    r = _run({"Ebirja Договоры (Э-магазин)": _iso(1)})
    assert r["status"] == "fail" and "Ebirja Договоры (Отбор) молчит всегда" in r["message"], r


def test_config_without_scripted_sources_is_ok():
    r = _run({}, yaml_text="sources:\n  - id: a\n    name: A\n    adapter: api\n    url: x\n    id_prefix: a\n")
    assert r["status"] == "ok", r


def test_scripted_check_is_supabase_dependent():
    assert "sources.scripted" in H.SUPABASE_DEPENDENT_COMPONENTS


def test_real_config_marks_all_four_ebirja_contract_sources():
    import yaml
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "sources.yaml")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    scripted = {s["name"]: s["collected_by"] for s in raw["sources"] if s.get("collected_by")}
    for kind in ("Э-магазин", "Аукцион", "Тендер", "Отбор"):
        assert scripted.get("Ebirja Договоры (%s)" % kind) == "fetch_ebirja_contracts", scripted
    assert not any(s.get("enabled", True) for s in raw["sources"] if s.get("collected_by")), \
        "runner их не обходит — пишет скрипт"


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
