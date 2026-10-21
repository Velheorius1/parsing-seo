"""«Фотоальбом» и «albom» ловятся, а не только «альбом» (21.10.2026).

ЧТО СЛУЧИЛОСЬ. «Альбом» в словаре с 08.10, но сопоставитель ищет слово с начала:
«фотоальбом» его не содержит. Заявки TG PR Media Group «изготовление фотоальбомов»
(06.08, 27.09) умерли на словах. Латинское «albom» (kitob-albom, «Plastik albom»)
тоже не было в словаре.

Run: .venv/bin/python3 -m pytest crawler/tests/test_keyword_album.py -q
"""
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.tests.test_keyword_logotip import KW  # noqa: E402


def _kw(title):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=""), KW)


def test_lost_tg_requests_are_caught():
    for title in ("Добрый день, подскажите кто занимается изготовлением фотоальбомов ?",
                  "Нужен исполнитель для изготовления фотоальбомов с твердым переплетом",
                  "Фотоальбом ИИБга (3 kvartal - 2026)"):
        assert _kw(title) is not None, title


def test_uzbek_latin():
    assert _kw("Plastik albom adresat papka va tabriknomlar") is not None
    assert _kw("nomli fotoalbomni tayyorlash") is not None


def test_plain_album_still_caught():
    assert _kw("Нужен альбом") is not None
