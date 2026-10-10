"""«Логотип» ловится во всех формах и на двух письменностях (10.10.2026).

ЧТО СЛУЧИЛОСЬ. TenderWeek №36638 «Сумки из спанбонда с нанесением логотипа» —
наш прямой профиль, срок 10.10 — дошёл до префильтра и умер на словах:
в словаре были «мерч», «шоппер», «брендиров», но не «логотип». За 60 дней
так же умерли 54 заявки клиентов в TG PR Media Group (флажки, фартуки,
салфетки, полотенца с логотипом).

ЧТО ПРИШИВАЕМ. Словарь — из самого settings.py (разбором исходника, как в
test_keyword_varaqa: в общем прогоне settings почти всегда заглушка). Формы с
окончаниями ловятся; соседние по написанию слова — нет.

Run: .venv/bin/python3 -m pytest crawler/tests/test_keyword_logotip.py -q
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


def _kw(title):
    return N._find_matching_keyword(types.SimpleNamespace(title=title, search_text=""), KW)


def test_parser_sees_real_dictionary():
    assert len(KW) > 100, len(KW)


def test_lost_tenderweek_lot_is_caught():
    assert _kw("Сумки из спанбонда с нанесением логотипа") is not None


def test_russian_forms_from_missed_requests():
    for title in ("Нужно сделать несколько флажков с логотипом",
                  "срочно пошить черный фартук с логотипом",
                  "Нужны полотенца с логотипом",
                  "Напечатать логотип 70 см на материале 7x5 метров"):
        assert _kw(title) is not None, title


def test_uzbek_latin_and_cyrillic():
    assert _kw("brend buk (brandbook) va logotip ishlab chiqish") is not None
    assert _kw("okruk logotipi") is not None
    assert _kw("Логотипга эга маҳсулотларни ишлаб чиқариш бўйича хизмат") is not None


def test_neighbours_by_spelling_are_not_caught():
    for title in ("Логистические услуги по доставке", "Услуги логопеда", "Лоток для бумаг"):
        assert _kw(title) is None, (title, _kw(title))


def test_bags_and_bare_application_were_left_out():
    assert _kw("Сумка для ноутбука") is None
    assert _kw("Услуга по нанесению пленки") is None
