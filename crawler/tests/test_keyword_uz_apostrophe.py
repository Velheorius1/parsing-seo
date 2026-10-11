"""Узбекский апостроф не режет слово: «sovga» находит «sovg‘alar» (11.10.2026).

ЧТО СЛУЧИЛОСЬ. o‘/g‘ пишутся апострофом, а сопоставитель считал апостроф
границей слова: «sovg‘alar» для него — «sovg» и «alar». За 29.09–09.10 мимо
словаря ушли 5 открытых лотов на ~700 млн: Минобороны «Qimmatbaho sovg‘alar
xarid qilish» ×3, UzAuto «Sovg‘ali to‘plamlar», ТВО «Sovg'a 5».

ЧТО ПРИШИВАЕМ. Словарь — из самого settings.py (как в test_keyword_logotip).
Заголовки — живые лоты этого замера; все пять написаний апострофа.

Run: .venv/bin/python3 -m pytest crawler/tests/test_keyword_uz_apostrophe.py -q
"""
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.tests.test_keyword_logotip import KW  # noqa: E402


def _kw(title, keywords=KW):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=""), keywords)


def test_missed_gift_lots_are_caught():
    for title in ("Qimmatbaho sovg‘alar xarid qilish",
                  "Sovg‘ali to‘plamlar",
                  "Sovg'a 5",
                  "sovg'alar-4-2026"):
        assert _kw(title) == "sovga", (title, _kw(title))


def test_every_apostrophe_spelling_is_the_same_word():
    for mark in ("'", "‘", "’", "ʻ", "ʼ", "`"):
        title = "Qimmatbaho sovg%salar" % mark
        assert _kw(title) == "sovga", (repr(mark), _kw(title))


def test_keyword_written_with_apostrophe_matches_any_spelling():
    assert _kw("Sovg‘ali toʻplamlar", ["to'plam"]) == "to'plam"


def test_choponota_is_not_chop():
    # «chop» (печать) ≠ «cho‘pon»: без апострофа махалля стала бы «choponota».
    assert _kw("Uchtepa tumani “Katta Cho‘ponota” mahalla fuqarolar yig‘ini hududida") is None
    assert _kw("Kitoblarni chop etish xizmati") is not None
