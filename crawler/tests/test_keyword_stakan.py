"""«Стакан» ловится по-русски, по-узбекски кириллицей и латиницей (14.10.2026).

ЧТО СЛУЧИЛОСЬ. Бумажные стаканы — наш профиль, но в словаре было только
«надстаканник». «Қоғоз стакан» в Cooperation (метка Данияра «клиент» 20.07;
37 млн 09.09, 6,8 млн 02.10) умирал на словах: его ловил старый отдельный
конвейер Cooperation, а при слиянии 22.07 слово не перенесли.

ЧТО ПРИШИВАЕМ. Словарь — из самого settings.py (как в test_keyword_logotip).
Формы с окончаниями ловятся; слово внутри другого («подстаканник») и соседи
по написанию — нет.

Run: .venv/bin/python3 -m pytest crawler/tests/test_keyword_stakan.py -q
"""
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.tests.test_keyword_logotip import KW  # noqa: E402


def _kw(title):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=""), KW)


def test_lost_cooperation_lot_is_caught():
    assert _kw("Қоғоз стакан") is not None


def test_russian_forms_from_missed_lots_and_requests():
    for title in ("Одноразовые бумажные стаканы вместимостью 200мл",
                  "Нужен производитель бумажных стаканчиков",
                  "Кто делает стаканчики с нанесением лого, минимальный тираж и цена",
                  "принт на одноразовые стаканчики"):
        assert _kw(title) is not None, title


def test_uzbek_latin():
    # без «qog'oz»: его уже ловит «qogoz», здесь держим именно «stakan»
    assert _kw("Bir martalik stakanlar 250 ml") is not None
    assert _kw("Stakan uchun qopqoq") is not None


def test_word_inside_another_word_is_not_caught():
    for title in ("Подстаканник латунный", "Станок токарный", "Стандартизация услуг"):
        assert _kw(title) is None, (title, _kw(title))
