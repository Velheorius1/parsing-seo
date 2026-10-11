"""«poligraf», «matbaa», «bezak» — узбекская латиница в словаре (11.10.2026).

ЧТО СЛУЧИЛОСЬ. Кириллическое «полиграф» латиницу не ловит. Два крупных лота
топ-100 лежали у нас в базе и умерли на словах (no_keyword):
Узбектелеком «Poligrafiya mahsulotalrini ishlab chiqarish va yetkazib berish»
690 млн и Шуртан «Bezak ishlari va matbaa xizmatlari» 794 млн.

ЧТО ПРИШИВАЕМ. Словарь — из самого settings.py (как в test_keyword_logotip).
Заголовки — эти два пропуска из purchase_ledger.

Run: .venv/bin/python3 -m pytest crawler/tests/test_keyword_uz_latin.py -q
"""
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.tests.test_keyword_logotip import KW  # noqa: E402


def _kw(title):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=""), KW)


def test_words_are_in_the_dictionary():
    for word in ("poligraf", "matbaa", "bezak"):
        assert word in KW, word


def test_uzbektelekom_poligrafiya_is_caught():
    assert _kw("Poligrafiya mahsulotalrini ishlab chiqarish va yetkazib berish") == "poligraf"


def test_shurtan_bezak_matbaa_is_caught():
    assert _kw("Bezak ishlari va matbaa xizmatlari") in ("bezak", "matbaa")
    assert _kw("Matbaa xizmatlari") == "matbaa"
