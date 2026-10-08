"""Первые слова из предложений системы (08.10, фаза 5 топ-100): пропуски ловятся.

ЧТО СЛУЧИЛОСЬ. Детектор пропусков (customer_miss) нашёл 33 покупки топ-100
нашего профиля от 20 млн, чей лот лежал у нас в базе и умер на ключевых словах:
«чоп этиш» кириллицей, «нашр», «jurnali», «Merchendayzing» Узбектелекома на
1,8 млрд. propose_fixes предложил слова с бэктестом, Данияр одобрил все
(learning_proposals 1–8).

ЧТО ПРИШИВАЕМ. Настоящие предметы пропущенных покупок ловятся живым словарём —
иначе вернётся та же потеря. Словарь читается из исходника settings.py (как в
test_keyword_varaqa): в общем прогоне настройки заменены заглушкой.

Run: python3 -m pytest crawler/tests/test_keyword_learned_1008.py
"""
import types

from crawler.tests._stubs import install_settings_stub
from crawler.tests.test_keyword_varaqa import _real_keywords

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402

KW = _real_keywords()
LEARNED = ("журнал", "jurnal", "чоп", "нашр", "nashr", "merchendayzing", "esdalik", "альбом")


def _kw(title):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=""), KW)


def test_learned_words_are_in_the_live_dictionary():
    assert len(KW) > 100
    for word in LEARNED:
        assert word in KW, word


def test_missed_top100_purchases_are_caught_now():
    # Предметы из purchase_ledger (miss_type A_filter, root_stage no_keyword).
    lost = {
        "Нашр этиш хизмати": "нашр",                                       # МЧС, 576 млн
        "чоп этиш хизмати": "чоп",                                          # 286 млн
        "Esdalik qo'l soati": "esdalik",                                    # 1,9 млрд
        "Merchendayzing mahsulotlarini ishlab chiqarish va yetkazib berish": "merchendayzing",
        "Tafakkur jurnalini 3 - soni": "jurnal",
        "Тил ва адабиёт таълими журнали 2026 йил 5 сони": "журнал",
        "OLIY TAʼLIM MUASSASALARI UCHUN ILMIY ADABIYOTNI NASHR ETISH BO‘YICHA": "nashr",
    }
    for title, word in lost.items():
        assert _kw(title) == word, (title, _kw(title))
    assert _kw('"Халқ билан хамнафас" альбом ва “Янги Ўзбекистон” китобларини нашр қилиш') is not None


def test_short_chop_matches_only_at_word_start():
    # «чоп» короче основы — ищется как начало слова, не внутри: «ачоп…» не ловится.
    assert _kw("Ачопт ускуна") is None
