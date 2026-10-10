"""Общая очередь запросов к одному бэкенду на ВСЕ процессы VPS (10.10.2026).

ЧТО СЛУЧИЛОСЬ. С 07.10 ~16:00 UTC шесть источников XT-Xarid и Hayotbirja
отдавали 0 строк: площадка отвечала «429 Too Many Requests». Замер 10.10:
с нашего VPS проходит ОДИН запрос в минуту — после успешного ответа запрос
через 48 с получает 429, через 61 с проходит. С домашнего IP пять запросов
подряд проходят все, то есть лимит висит на IP сервера, а не на площадке
целиком. И он общий на оба домена: api.xt-xarid.uz и api.hayotbirja.uz —
один бэкенд (100% совпадение id по всем четырём лентам, замер 10.10).

ПОЧЕМУ СТАРЫЙ ОГРАНИЧИТЕЛЬ НЕ ПОМОГАЛ. `rate_limiter` живёт внутри одного
процесса и считает домены порознь: два домена одного бэкенда шли параллельно,
а сторож наших лотов (cron */2), краул встречных аукционов (*/20) и основной
краул — это разные процессы, которые друг о друге не знают. В начале каждого
чётного часа они стартовали в одну секунду; проходил один запрос из пачки,
остальные ловили 429, а повторы через 2 и 4 секунды были обречены.

КАК УСТРОЕНО. Файл состояния с моментом следующего свободного слота и
файловый замок вокруг него. Процесс под замком берёт ближайший слот, сдвигает
«следующий свободный» на интервал вперёд и отпускает замок — ждёт он уже без
замка. Так запросы всех процессов выстраиваются в одну очередь, а не дерутся.
Если ждать дольше `max_wait`, слот не занимается вовсе: вызывающий решает сам,
пропустить ли ход.

После 429 (окно занял кто-то мимо очереди — верификатор, ручной curl) зовётся
`penalize`: следующий свободный слот сдвигается на целый интервал от «сейчас».
"""

import asyncio
import fcntl
import json
import logging
import os
import time
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

XT_BACKEND = "xt-backend"

# Хосты одного бэкенда. SPA-домены тоже здесь: страница в браузере ходит в тот
# же API с того же IP.
_HOST_GROUPS = {
    "api.xt-xarid.uz": XT_BACKEND,
    "api.hayotbirja.uz": XT_BACKEND,
    "xt-xarid.uz": XT_BACKEND,
    "hayotbirja.uz": XT_BACKEND,
}

# Интервал между запросами, секунды. 62 = замеренная минута + запас на рассинхрон
# часов. Переопределяется переменной окружения, если площадка лимит сменит.
_DEFAULT_INTERVALS = {XT_BACKEND: 62.0}
_ENV_INTERVAL = {XT_BACKEND: "PARSING_XT_INTERVAL_S"}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def backend_for(url):
    # type: (str) -> Optional[str]
    """Ключ общей очереди для URL или None, если хост ни с кем не делит лимит."""
    host = (urlparse(url or "").netloc or "").lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return _HOST_GROUPS.get(host)


def interval_for(key):
    # type: (str) -> float
    env = _ENV_INTERVAL.get(key)
    if env and os.environ.get(env):
        try:
            return max(1.0, float(os.environ[env]))
        except ValueError:
            logger.warning("[HostBudget] %s=%r не число — беру %.0f с",
                           env, os.environ[env], _DEFAULT_INTERVALS.get(key, 60.0))
    return _DEFAULT_INTERVALS.get(key, 60.0)


def _state_path(key, state_dir=None):
    # type: (str, Optional[str]) -> str
    base = state_dir or os.environ.get("PARSING_HOST_BUDGET_DIR") or os.path.join(_REPO_ROOT, "logs")
    return os.path.join(base, "host_budget_%s.json" % key)


def _locked_update(key, update, state_dir=None):
    """Прочитать next_free под замком, вызвать update(next_free) -> (new_next_free|None, result)."""
    path = _state_path(key, state_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            raw = f.read()
            try:
                next_free = float(json.loads(raw).get("next_free", 0.0)) if raw.strip() else 0.0
            except (ValueError, AttributeError, TypeError):
                next_free = 0.0
            new_next_free, result = update(next_free)
            if new_next_free is not None:
                f.seek(0)
                f.truncate()
                f.write(json.dumps({"next_free": new_next_free}))
                f.flush()
            return result
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def reserve(key, now=None, max_wait=None, state_dir=None):
    # type: (str, Optional[float], Optional[float], Optional[str]) -> Optional[float]
    """Занять ближайший свободный слот. Вернуть момент (time.time()), когда слать запрос.

    None — слот дальше `max_wait` секунд; в этом случае очередь не трогается.
    """
    t = time.time() if now is None else now
    step = interval_for(key)

    def _update(next_free):
        slot = max(t, next_free)
        if max_wait is not None and slot - t > max_wait:
            return None, None
        return slot + step, slot

    return _locked_update(key, _update, state_dir)


def penalize(key, now=None, state_dir=None):
    # type: (str, Optional[float], Optional[str]) -> None
    """После 429: окно уже занято кем-то мимо очереди — следующий слот не раньше чем через интервал."""
    t = time.time() if now is None else now
    step = interval_for(key)
    _locked_update(key, lambda next_free: (max(next_free, t + step), None), state_dir)


async def acquire(key, max_wait=None):
    # type: (str, Optional[float]) -> bool
    """Дождаться своего слота. False — очередь длиннее `max_wait`, запрос слать нельзя."""
    slot = reserve(key, max_wait=max_wait)
    if slot is None:
        return False
    delay = slot - time.time()
    if delay > 0:
        await asyncio.sleep(delay)
    return True
