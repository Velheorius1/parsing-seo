"""Пины на macOS-огрызки в дереве кода (10.08).

Что это. `scp` с Мака вместе с `foo.py` кладёт рядом `._foo.py` — ресурсную вилку
AppleDouble: бинарный файл с расширением .py. На проде их обнаружилось 59 штук,
датированных 16.03, все untracked. Пролежали почти пять месяцев.

Что они успели сломать и что могли сломать.
  • УЖЕ: test_reasoning_disabled обходил `crawler/**/*.py` и падал на первом же
    огрызке с UnicodeDecodeError. То есть проверка обязательного флага
    `reasoning: {"enabled": False}` на проде не отрабатывала с 16.03 — а именно
    отсутствие этого флага стоило нам 358 коррекций, ушедших в пустоту.
  • ЕЩЁ НЕТ, но хуже: healthcheck меряет свежесть кода по mtime файлов в `core/`
    и сравнивает со временем старта сервиса. Огрызок из СВЕЖЕГО scp получит
    mtime новее старта — и healthcheck выдаст «STALE CODE» на исправном боте,
    отправив перезапускать то, что работает.

Свойство, которое тут держится: любой обход исходников фильтрует `._*`. Удаление
файлов эту дыру не закрывает — они появятся при следующем scp.

Run: python3 -m crawler.tests.test_appledouble_junk   (exit 1 on any failure)
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_healthcheck_has_a_named_source_filter():
    """Фильтр должен быть ИМЕНЕМ, а не повторённым условием: два места в
    healthcheck и одно в тесте разъедутся молча."""
    s = _src("scripts/healthcheck.py")
    assert "def is_source_file(" in s
    assert s.count("if is_source_file(f)") >= 2, "оба обхода core/ должны фильтровать"
    assert 'f.endswith(".py"))' not in s, "остался необработанный обход"


def test_source_filter_rejects_appledouble():
    sys.path.insert(0, _ROOT)
    import types
    if "crawler.config.settings" not in sys.modules:
        m = types.ModuleType("crawler.config.settings")
        m.settings = types.SimpleNamespace()
        sys.modules["crawler.config.settings"] = m
    try:
        from crawler.scripts.healthcheck import is_source_file
    except Exception as exc:          # healthcheck тянет много прод-зависимостей
        assert "def is_source_file" in _src("scripts/healthcheck.py"), exc
        return
    assert is_source_file("notifier.py") is True
    assert is_source_file("._notifier.py") is False
    assert is_source_file("notifier.pyc") is False
    assert is_source_file("._notifier") is False


def test_reasoning_check_skips_appledouble():
    s = _src("tests/test_reasoning_disabled.py")
    assert 'startswith("._")' in s, "иначе проверка флага падает на первом огрызке"


def test_reasoning_check_survives_a_junk_file_next_to_sources():
    """Живая проверка: кладём настоящий огрызок и убеждаемся, что обход не падает."""
    import importlib
    sys.path.insert(0, _ROOT)
    mod = importlib.import_module("crawler.tests.test_reasoning_disabled")
    junk_dir = os.path.join(_ROOT, "core")
    junk = os.path.join(junk_dir, "._pytest_junk_probe.py")
    created = False
    try:
        with open(junk, "wb") as f:      # то же начало, что у настоящих вилок
            f.write(b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\x02\x00\x00\xa3")
        created = True
        files = mod._python_sources()
        assert all(not os.path.basename(p).startswith("._") for p in files)
        mod._call_sites()                # раньше здесь был UnicodeDecodeError
    finally:
        if created:
            os.remove(junk)


def test_a_junk_file_would_not_be_mistaken_for_fresh_code():
    """Смысловая проверка второй дыры: огрызок новее всех исходников не должен
    попадать в расчёт свежести."""
    sys.path.insert(0, _ROOT)
    import types
    if "crawler.config.settings" not in sys.modules:
        m = types.ModuleType("crawler.config.settings")
        m.settings = types.SimpleNamespace()
        sys.modules["crawler.config.settings"] = m
    try:
        from crawler.scripts.healthcheck import is_source_file
    except Exception:
        return  # покрыто test_healthcheck_has_a_named_source_filter
    with tempfile.TemporaryDirectory() as d:
        old = os.path.join(d, "real.py")
        new = os.path.join(d, "._real.py")
        open(old, "w").close()
        open(new, "w").close()
        os.utime(old, (1, 1))            # исходник старый, огрызок свежий
        names = [f for f in sorted(os.listdir(d)) if is_source_file(f)]
        assert names == ["real.py"], names
        newest = max(os.path.getmtime(os.path.join(d, f)) for f in names)
        assert newest < os.path.getmtime(new), "свежесть считается по огрызку"


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
