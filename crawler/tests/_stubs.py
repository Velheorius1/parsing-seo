"""Общий способ подменять модуль в тестах — так, как это делает настоящий импорт.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Половина тестов репозитория подменяет прод-зависимости
(`crawler.auth.session_store`, `crawler.config.settings`, `crawler.core.db`),
записывая заглушку прямо в `sys.modules`. Настоящий импорт делает ДВА действия:
кладёт модуль в `sys.modules` И привязывает его атрибутом к родительскому пакету.
Ручная подмена делала только первое — и на этом ломались чужие тесты.

КАК ЭТО ВЫГЛЯДЕЛО. `test_results_tracker_dedup` подменяет хранилище строковой
целью: `monkeypatch.setattr("crawler.auth.session_store.session_store", fake)`.
Pytest разбирает такую цель обходом атрибутов: `crawler` -> `auth` -> `session_store`.
Если заглушка лежит только в `sys.modules`, обход спотыкается на втором шаге:

    AttributeError: module 'crawler.auth' has no attribute 'session_store'

Поодиночке файл проходил, в общем прогоне падал — то есть виноват был не он, а
любой из четырёх модулей, отработавших раньше по алфавиту. 18 падающих тестов
держались на этом с рождения соответствующих файлов.

ПОЧЕМУ НЕ ЧИНИТЬ В ЖЕРТВЕ. Чинить пришлось бы в каждой следующей жертве заново,
а список заглушек растёт. Дефект — в способе подменять, поэтому чинится способ.
"""
import importlib
import sys
import types


def install_stub(name, **attrs):
    # type: (str, object) -> types.ModuleType
    """Подменить модуль `name` заглушкой с атрибутами `attrs`.

    Идемпотентно: если модуль уже в `sys.modules` (настоящий или чужая заглушка),
    возвращаем его нетронутым — тесты рассчитывают на «первый пришёл, того и
    заглушка», и молча переписать чужую было бы хуже исходной болезни.

    Привязка к родителю обязательна: без неё модуль есть для `import`, но нет
    для обхода атрибутов, которым пользуются `mock.patch` и `monkeypatch.setattr`
    со строковой целью.
    """
    existing = sys.modules.get(name)
    if existing is not None:
        _bind_to_parent(name, existing)
        return existing
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    _bind_to_parent(name, mod)
    return mod


def _bind_to_parent(name, mod):
    # type: (str, types.ModuleType) -> None
    """`crawler.auth.session_store` -> выставить `session_store` на `crawler.auth`."""
    parent_name, _, child = name.rpartition(".")
    if not parent_name:
        return
    parent = sys.modules.get(parent_name)
    if parent is None:
        try:
            parent = importlib.import_module(parent_name)
        except Exception:
            return  # родителя нет вовсе — привязывать некуда, это не наша беда
    setattr(parent, child, mod)
