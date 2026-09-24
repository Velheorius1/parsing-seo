"""Бланки по-узбекски: «варақаси/варақалар» ловятся, «варақ» (лист) — нет (24.09).

ЧТО СЛУЧИЛОСЬ. Лот ETender 510150 «Жавоблар варакаси_28.08.2026» — бланки
ответов для Агентства оценки знаний на 162.9 млн сум — лежал в базе с 03.09 и
не дошёл до AI-гейта: ни один ключевик не совпал. Выиграл конкурент из реестра.

ЧТО ПРИШИВАЕМ. Словарь берётся из самого settings.py (разбором исходника, а не
импортом: в общем прогоне `crawler.config.settings` почти всегда уже заменён
заглушкой с пустым словарём, и тест проверял бы пустоту). Две стороны:
  • формы с окончанием ловятся — иначе вернётся та же потеря;
  • голое «варақ» не ловится — иначе вернётся шум, который дал замер широкой
    формы (24 совпадения за 14 дней, по делу одно).

Run: python3 -m pytest crawler/tests/test_keyword_varaqa.py
"""
import ast
import os
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402

_SETTINGS_PY = os.path.join(os.path.dirname(__file__), "..", "config", "settings.py")


def _real_keywords():
    tree = ast.parse(open(_SETTINGS_PY, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "alert_keywords":
            words = [c.value for c in ast.walk(node.value)
                     if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value != ","]
            return [w.strip().lower() for w in words if w.strip()]
    raise AssertionError("alert_keywords не найден в settings.py — тест ослеп")


KW = _real_keywords()


def _kw(title, search_text=""):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=search_text), KW)


def test_parser_sees_real_dictionary():
    # Защита от слепоты: если разбор сломается, словарь окажется пустым и все
    # «не ловится» ниже пройдут зря.
    assert len(KW) > 100, len(KW)
    assert "бланк" in KW


def test_lost_lot_is_caught():
    assert _kw("Жавоблар варакаси_28.08.2026") == "варакас"


def test_uzbek_forms_are_caught():
    assert _kw("Жавоб варақалари") == "варақал"
    assert _kw("Baholash varaqasi") == "varaqas"
    assert _kw("Javob varaqalari") == "varaqal"


def test_bare_sheet_is_not_caught():
    # «варақ» = «лист»: именно это слово дало шум в замере широкой формы.
    assert _kw("Файл варақ 0,60 микрон 100 дона") is None
    assert _kw("Шифер", "Том ёпиш учун асбест-цемент шифер 8 варақ") is None


def test_stemmer_keeps_forms_whole():
    # Стеммер режет окончания только у кириллических «а/о/и…»; эти формы
    # оканчиваются на «с/л» и должны остаться целыми, иначе расширятся до «варақ».
    for w in ("варақас", "варақал", "варакас", "варакал"):
        assert N._stem(w) == w, w
