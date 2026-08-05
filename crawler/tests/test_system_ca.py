"""Пины опции `use_system_ca` (05.08).

Зачем: OSCE отдаёт цепочку с корнем, которого нет в certifi, httpx падал с
CERTIFICATE_VERIFY_FAILED, и источник молчал 28 дней — по логам это читалось
как «нечего собирать», ровно тот класс, где отсутствие сигнала принимают за
отсутствие события.

Свойства, которые здесь держатся:
  • по умолчанию ничего не меняется — verify=True (certifi);
  • с флагом отдаётся ПУТЬ к системному набору, а не False: проверка
    сертификата не должна отключаться никогда;
  • если системного файла нет, честно возвращаемся к certifi и предупреждаем,
    а не делаем вид, что настройка применилась.

Run: python3 -m crawler.tests.test_system_ca   (exit 1 on any failure)
"""
import os
import sys
import types


def _load():
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(residential_proxy_url="")
        sys.modules[cfg] = m
    from crawler.adapters.html import _ca_bundle, _SYSTEM_CA_PATHS
    return _ca_bundle, _SYSTEM_CA_PATHS


_ca_bundle, _SYSTEM_CA_PATHS = _load()


def _cfg(**kw):
    base = {"name": "тест"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_default_is_certifi():
    assert _ca_bundle(_cfg(use_system_ca=False)) is True


def test_missing_attribute_is_treated_as_off():
    """Старые конфиги без поля не должны падать."""
    assert _ca_bundle(_cfg()) is True


def test_enabled_returns_existing_path_not_false():
    got = _ca_bundle(_cfg(use_system_ca=True))
    if any(os.path.exists(p) for p in _SYSTEM_CA_PATHS):
        assert isinstance(got, str) and os.path.exists(got), got
    else:
        assert got is True, got
    assert got is not False, "проверку сертификата отключать нельзя"


def test_falls_back_to_certifi_when_no_system_store():
    """Mac разработчика: системного файла нет — возвращаемся к certifi."""
    import crawler.adapters.html as H
    orig = H._SYSTEM_CA_PATHS
    H._SYSTEM_CA_PATHS = ("/nonexistent/ca-1.crt", "/nonexistent/ca-2.crt")
    try:
        assert H._ca_bundle(_cfg(use_system_ca=True)) is True
    finally:
        H._SYSTEM_CA_PATHS = orig


def test_osce_source_has_the_flag():
    """Ради чего опция и заведена — пин, чтобы правку не потеряли."""
    import os as _os
    import yaml
    path = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "config", "sources.yaml")
    raw = yaml.safe_load(open(path))
    osce = [s for s in raw["sources"] if s.get("id") == "osce-uz"]
    assert osce, "источник osce-uz пропал из конфига"
    assert osce[0].get("use_system_ca") is True


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
