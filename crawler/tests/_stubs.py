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
import contextlib
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


# Полный набор полей `settings`, которые читает прод-код. Собран grep'ом по
# crawler/core и crawler/scripts (05.09).
#
# ЗАЧЕМ ОН ЗДЕСЬ. `install_stub` идемпотентен — заглушку ставит тот файл, что
# отработал раньше по алфавиту. Пока у каждого была своя, набор полей зависел
# от порядка: новый test_alert_escalation встал перед test_notifier_relevance
# с пятью полями вместо девяти и уронил десять чужих тестов на
# `AttributeError: no attribute 'ai_score_threshold'`. Дефект не в жертве и не
# в новичке, а в том, что заглушка настроек у каждого своя.
_SETTINGS_DEFAULTS = {
    "ai_eval_enabled": False,
    "ai_evaluator_model": "stub-model",
    "ai_relevance_model": "stub-model",
    "ai_relevance_model_fast": "stub-model-fast",
    "ai_score_threshold": 70,
    "alert_keywords": "",
    "batch_size": 50,
    "openrouter_api_key": "",
    "residential_proxy_url": "",
    "supabase_service_role_key": "",
    "supabase_url": "",
    "telegram_alert_chat_id": "",
    "telegram_bot_token": "",
    "tnved_scope": "",
}


def install_settings_stub(**overrides):
    # type: (object) -> types.ModuleType
    """Заглушка `crawler.config.settings` с ПОЛНЫМ набором полей.

    Если модуль уже подменён кем-то другим, недостающие поля дописываются в
    существующую заглушку: чужие ожидания не ломаются, а свои выполняются.
    Настоящий `settings` (не заглушку) не трогаем.
    """
    attrs = dict(_SETTINGS_DEFAULTS)
    attrs.update(overrides)
    mod = install_stub("crawler.config.settings",
                       settings=types.SimpleNamespace(**attrs))
    existing = getattr(mod, "settings", None)
    if existing is not None and getattr(mod, "__file__", None) is None:
        for key, value in attrs.items():
            if not hasattr(existing, key):
                setattr(existing, key, value)
    return mod


_MISSING = object()


@contextlib.contextmanager
def swapped_modules(stubs):
    # type: (dict) -> object
    """Подменить модули ТОЛЬКО на время блока `with` и вернуть всё как было.

    ЧЕМ ОТЛИЧАЕТСЯ ОТ install_stub. Тот ставит заглушку на весь процесс по
    правилу «первый пришёл» — это годится для зависимости, которую модуль
    читает при своём импорте: ссылка захвачена, дальше неважно, что лежит в
    `sys.modules`. Но код, который берёт зависимость ЛЕНИВО, внутри функции
    (`from crawler.core.db import query_with_retry` в теле `_ids_present`),
    видит ту заглушку, что лежит в `sys.modules` в МОМЕНТ ВЫЗОВА. А при общем
    прогоне там сидит заглушка последнего собранного по алфавиту файла.

    КАК ЭТО ВЫГЛЯДЕЛО (22-24.09). test_coop_id_batches на уровне модуля
    записал `sys.modules["crawler.core.db"] = пустой модуль`. Он собирается по
    алфавиту раньше пяти файлов, которым нужен настоящий `_get_client`, — те
    упали на импорте, а pytest при ошибках сбора не запустил НИ ОДНОГО теста.
    Сами же его три теста в общем прогоне падали по обратной причине: к моменту
    вызова слот уже перезаписал test_replay_pure своей взрывной заглушкой
    («side-effect path touched from replay»). Поодиночке всё проходило.

    Подмена и возврат делаются и в `sys.modules`, и атрибутом у родителя —
    по той же причине, что в install_stub.
    """
    saved = []
    try:
        for name, mod in stubs.items():
            parent_name, _, child = name.rpartition(".")
            parent = sys.modules.get(parent_name) if parent_name else None
            prev_parent_attr = getattr(parent, child, _MISSING) if parent is not None else _MISSING
            saved.append((name, sys.modules.get(name, _MISSING), parent, child, prev_parent_attr))
            sys.modules[name] = mod
            if parent is not None:
                setattr(parent, child, mod)
        yield
    finally:
        for name, prev, parent, child, prev_parent_attr in reversed(saved):
            if prev is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
            if parent is not None:
                if prev_parent_attr is _MISSING:
                    try:
                        delattr(parent, child)
                    except AttributeError:
                        pass
                else:
                    setattr(parent, child, prev_parent_attr)
