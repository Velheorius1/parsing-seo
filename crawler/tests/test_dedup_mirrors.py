"""Пины связки xt-xarid.uz ↔ hayotbirja.uz общим dedup_group (05.08).

Что это. Один бэкенд под двумя доменами: тот же путь /procedure/tender, та же
разметка, те же id лотов. Замер по всей истории: 91 общий external_id,
уникальных у hayotbirja НОЛЬ, у xt-xarid один — второй строго вложен в первый.
Общая группа схлопывает строки до одной на лот, но опрашиваются оба: домены
площадки падают порознь, и зеркало — резерв.

Чем это опасно и что здесь держится:
  • Победителя выбирает ПОРЯДОК в конфиге («first-encountered source wins»
    в runner.py). Переставят блоки — победитель молча поменяется, и уникальная
    строка xt-xarid потеряется. Порядок пинится тестом.
  • Проигравший всегда даёт 0 новых строк. 28.04 такую же общую группу уже
    разделяли, когда молчание `XT-Xarid тендеры` приняли за поломку. Чтобы не
    повторилось, зеркало внесено в `DEDUP_MIRRORS` сторожа — и это тоже пин.

Run: python3 -m crawler.tests.test_dedup_mirrors   (exit 1 on any failure)
"""
import os
import sys
import types

import yaml

CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "config", "sources.yaml")
RAW = yaml.safe_load(open(CFG))
SOURCES = RAW["sources"]
BY_ID = {s["id"]: s for s in SOURCES}
ORDER = [s["id"] for s in SOURCES]

PRIMARY = "xt-xarid"
MIRROR = "hayotbirja"
GROUP = "xtx-spa-tender"


def _load_watchdog():
    for name, attrs in (
        ("crawler.auth.session_store",
         {"session_store": types.SimpleNamespace(get_setting=lambda k: None,
                                                 set_setting=lambda k, v: True)}),
        ("crawler.config.settings",
         {"settings": types.SimpleNamespace(telegram_bot_token="",
                                            telegram_alert_chat_id="",
                                            supabase_url="",
                                            supabase_service_role_key="")}),
    ):
        if name not in sys.modules:
            m = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(m, k, v)
            sys.modules[name] = m
    import crawler.scripts.freshness_watchdog as W
    return W


W = _load_watchdog()


def test_both_share_the_group():
    assert BY_ID[PRIMARY].get("dedup_group") == GROUP
    assert BY_ID[MIRROR].get("dedup_group") == GROUP


def test_primary_comes_first_in_config():
    """Победителя выбирает порядок — перестановка блоков меняет его молча."""
    assert ORDER.index(PRIMARY) < ORDER.index(MIRROR), (
        "xt-xarid должен идти выше hayotbirja: он надмножество, "
        "и cross-source дедуп оставляет первый встреченный источник")


def test_both_stay_enabled():
    """Зеркало держим включённым — это резерв по домену, а не мусор."""
    assert BY_ID[PRIMARY].get("enabled") is True
    assert BY_ID[MIRROR].get("enabled") is True


def test_group_is_not_shared_with_anyone_else():
    """Группа только на эту пару: лишний участник — это потеря строк."""
    members = sorted(s["id"] for s in SOURCES if s.get("dedup_group") == GROUP)
    assert members == sorted([PRIMARY, MIRROR]), members


def test_mirror_is_known_to_the_watchdog():
    """Иначе ноль новых строк у зеркала прочитается как смерть источника."""
    assert BY_ID[MIRROR]["name"] in W.DEDUP_MIRRORS


def test_primary_is_not_muted_by_mistake():
    """Основной источник глушить нельзя — он и есть носитель данных."""
    assert BY_ID[PRIMARY]["name"] not in W.DEDUP_MIRRORS
    assert BY_ID[PRIMARY]["name"] not in W.KNOWN_RETIRED


def test_mirrors_and_retired_do_not_overlap():
    """Разные смыслы: retired — источника нет, mirror — учтён под другим именем."""
    assert not (W.DEDUP_MIRRORS & W.KNOWN_RETIRED)


def test_jsonrpc_pair_keeps_its_split_groups():
    """28.04 их РАЗДЕЛИЛИ осознанно — сюда лезть не надо, это другой случай."""
    assert BY_ID["xt-xarid-tender"].get("dedup_group") == "xtx-tender"
    assert BY_ID["hayotbirja-tender"].get("dedup_group") == "birja-tender"


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
