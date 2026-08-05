"""Пин: правило «битый SPA» не должно съедать рабочие ссылки (04.08).

Правило заведено по ПРЕФИКСУ «Cooperation.uz» и по умолчанию это верно: у
`Лоты` deep-link упирается в auth-wall, у предквалификаций резолвится в чужую
карточку — сырой URL там дезориентирует сильнее, чем его отсутствие. Но
префикс не различает маршруты, а они у площадки разные, и рабочая ссылка на
план закупки выбрасывалась из алерта: человеку оставались наша страница-архив
и поиск по ПЕРВОМУ СЛОВУ названия. На узбекоязычном «bosma kitoblar» это поиск
по слову «bosma» — то есть «найди сам среди всей печати страны».

Что проверено перед добавлением исключения (иначе не добавлять):
  1. маршрут `/plan-schedule/:id` есть в бандле SPA и публичен (`requiresAuth:!1`);
  2. `schedule-plan/schedule-plans/for-client/detail/{guid}` отдаёт карточку
     без авторизации;
  3. страница РЕНДЕРИТСЯ — headless Chromium через резидентный прокси, потому
     что SPA отдаёт HTTP 200 на любой путь и по коду рабочий маршрут от
     мёртвого не отличить (тот же капкан был с аукционами UZEX в W32).

Run: python3 -m crawler.tests.test_broken_spa_scope   (exit 1 on any failure)
"""
import sys

from crawler.core.snap import is_broken_spa


def test_verified_plan_source_is_not_broken():
    assert not is_broken_spa("Cooperation.uz Закупочные планы (filtered)")


def test_verified_auction_source_is_not_broken():
    """05.08: /auction/{числовой id} — рендер 3 из 3 открыл именно наш лот."""
    assert not is_broken_spa("Cooperation.uz Аукционы")


def test_rest_of_cooperation_stays_broken():
    """Исключения точечные — остальная площадка остаётся под правилом.

    Все проверены рендером 05.08 и отклонены: у `Лоты` и `Оферты` карточка
    открывается модалкой без смены URL (в DOM нет <a href> на карточку),
    у `Э-магазин лоты` маршрут /e-shop/:id адресует товар каталога
    (id ~180898), а не лот в торгах (id ~13744).
    """
    for src in ("Cooperation.uz Лоты", "Cooperation.uz Оферты",
                "Cooperation.uz Контракты", "Cooperation.uz Э-магазин лоты",
                "Cooperation.uz Закупочные планы"):
        assert is_broken_spa(src), src


def test_explicitly_listed_sources_stay_broken():
    for src in ("Xarid Конкурсы", "Xarid Прямые закупки", "xt-xarid.uz"):
        assert is_broken_spa(src), src


def test_unrelated_sources_are_not_touched():
    for src in ("Anor Bank", "UZEX Предквалификации", "XT-Xarid э-магазин",
                "ETender UZEX"):
        assert not is_broken_spa(src), src


def test_empty_source_is_safe():
    assert not is_broken_spa("")
    assert not is_broken_spa(None)


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
