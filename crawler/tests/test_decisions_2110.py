"""Решения Данияра 21.10.2026 после аудита «слепых убеждений».

1. П/э пакеты без логотипа — НАШ профиль. Правило playbook #186 «пакеты без
   нанесения = хозтовары» (выведено из 11 кликов «мимо») спорило с промптом
   «Пакеты — МЫ ДЕЛАЕМ» и за 30 дней отсеяло 20 лотов на 716 млн; #186 снято
   (status=retired), промпт говорит прямо.
2. Одежда и униформа с логотипом — НАШ профиль (отказы на 335,5 и 115,7 млн).
3. Баннеры и широкоформат — НЕ наш (в промпте уже было).
4. Лоты э-магазина Cooperation — спрос (заказчик выбрал товар, торги 48 ч):
   пуш, а не только дайджест; промпт больше не говорит модели «объявление
   поставщика, одежда = irrelevant».
+ Сторожа: живые разделы не прощены списками мёртвых/выведенных.

Run: .venv/bin/python3 -m pytest crawler/tests/test_decisions_2110.py -q
"""
from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.core.models import RawTender  # noqa: E402


def _t(source, **kw):
    base = dict(id="x-1", external_id="1", title="Полиэтиленовые пакеты (1000 шт)", organization="",
                source=source, source_url="https://x")
    base.update(kw)
    return RawTender(**base)


def test_prompt_says_plain_bags_and_logo_clothing_are_ours():
    p = N._RELEVANCE_PROMPT
    mine = p[p.index("МЫ ДЕЛАЕМ"):p.index("НЕ НАШЕ")]
    assert "без логотипа" in mine and "Пакеты" in mine, mine
    assert "Одежда и униформа с логотипом" in mine, mine
    assert "Баннеры" in p[p.index("НЕ НАШЕ"):], "баннеры остаются не нашими"


def test_cooperation_eshop_lot_is_demand_and_pushed():
    t = _t("Cooperation.uz Э-магазин лоты")
    assert N._is_high_signal(t) is True
    note = N._source_note(t.source)
    assert "ЗАКАЗЧИК выбрал" in note and "ПОСТАВЩИКА" not in note, note


def test_supplier_catalogs_stay_digest_only():
    for src in ("XT-Xarid э-магазин", "UZEX Э-магазин бумага и изделия", "Cooperation.uz Оферты"):
        assert N._is_high_signal(_t(src, price=500000000.0)) is False, src
    assert "ПОСТАВЩИКА" in N._source_note("XT-Xarid э-магазин")


def test_live_sources_are_not_excused_by_lists():
    from crawler.core.source_health import DEAD_SOURCES_WHITELIST, RETIRED_SOURCES
    for name in ("Cooperation.uz Аукционы", "Cooperation.uz Э-магазин лоты"):
        assert name not in DEAD_SOURCES_WHITELIST, name
    assert "E-Birja активные аукционы (xarid)" not in RETIRED_SOURCES
    assert "E-Birja активные аукционы (xarid)" not in DEAD_SOURCES_WHITELIST
