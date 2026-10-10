"""«Промо» ловит только слова на «промо…», не «пром…» (10.10.2026).

ЧТО СЛУЧИЛОСЬ. Стеммер резал «промо» до «пром», и слово ловило любую
промышленность. 60 дней прода (все ленты, кроме э-магазинов): 169 лотов
прошли словарь ТОЛЬКО через «промо», из них 162 мусорных: «промышленн…» в
141, «промежуточн…» в 15, «промыв…» в 10, ещё голое «пром.». AI ни одного не
пропустил, но 55 раз проверял зря. Настоящих 7: промостойки из заявок TG,
«промо-стойка», «промо форма», проморолик — все начинаются с «промо», и
после правки через «промо» проходят ровно они.

ЧТО ПРИШИВАЕМ. Словарь — из самого settings.py (как в test_keyword_logotip).
Заголовки — живые лоты из этого замера.

Run: .venv/bin/python3 -m pytest crawler/tests/test_keyword_promo.py -q
"""
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.tests.test_keyword_logotip import KW  # noqa: E402


def _kw(title):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=""), KW)


def test_promo_is_still_in_the_dictionary():
    assert "промо" in KW


def test_real_promo_requests_are_caught():
    for title in ("Нужны овальные разборные промостойки без обклейки",
                  "Нужен исполнитель для изготовления промостойки и роллапа",
                  "Подрядчик на изготовление промо-стойки, фотобудки и стоек для QR-кодов",
                  "Пошив промо формы и одежды"):
        assert _kw(title) == "промо", (title, _kw(title))


def test_industry_words_on_prom_are_not_caught():
    for title in ("Промывочное масло",
                  "Поставка промышленного оборудования",
                  "Ремонт промышленных кондиционеров",
                  "Промежуточная приёмка выполненных работ",
                  "Услуги по промывке системы отопления"):
        assert _kw(title) is None, (title, _kw(title))


def test_other_words_keep_their_stems():
    # Исключение точечное: остальной словарь режется как раньше.
    assert _kw("Изготовление упаковки для конфет") is not None
    assert _kw("Печатная продукция для выставки") is not None
