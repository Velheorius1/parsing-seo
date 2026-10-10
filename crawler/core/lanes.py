"""Отдельные очереди прогона источников (10.10.2026).

Зачем. Основной краул (`run_crawl.sh`, раз в 2 часа) запускает источники
параллельно и ждёт всех, прежде чем слать алерты. Источники бэкенда XT-Xarid
с 07.10 могут делать один запрос в минуту — двадцать страниц это двадцать
минут, и столько же ждали бы алерты всех остальных площадок. Поэтому такие
источники помечены `lane: xt-backend` и идут своим cron через
`crawler.main --lane xt-backend`, а здесь решается, кому из них пора.

Частота — `every_minutes` у источника; без него источник идёт каждый прогон
очереди. Момент последнего запуска хранится в маленьком JSON рядом с другими
кэшами (logs/), отмечается ПОСЛЕ прогона: упавший прогон повторится на
следующем тике cron, а не через `every_minutes`.
"""

import json
import logging
import os
import time
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Запас на дрожание cron: прогон в 10:00:40 после прогона в 08:00:05 — это «2 часа».
_JITTER_S = 120


def state_path(lane, state_dir=None):
    # type: (str, Optional[str]) -> str
    base = state_dir or os.path.join(_REPO_ROOT, "logs")
    return os.path.join(base, "lane_%s.json" % lane)


def load_state(lane, state_dir=None):
    # type: (str, Optional[str]) -> Dict[str, float]
    path = state_path(lane, state_dir)
    try:
        with open(path) as f:
            raw = json.load(f)
    except (IOError, OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}  # type: Dict[str, float]
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def save_state(lane, state, state_dir=None):
    # type: (str, Dict[str, float], Optional[str]) -> None
    path = state_path(lane, state_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, sort_keys=True)
    os.replace(tmp, path)


def lane_sources(sources, lane):
    """Включённые источники этой очереди, в порядке конфига."""
    return [s for s in sources if getattr(s, "lane", None) == lane]


def due_ids(sources, state, now=None):
    # type: (Iterable, Dict[str, float], Optional[float]) -> List[str]
    """id источников очереди, которым пора, в порядке конфига."""
    t = time.time() if now is None else now
    out = []  # type: List[str]
    for s in sources:
        every = getattr(s, "every_minutes", None)
        if not every or s.id not in state:
            out.append(s.id)
            continue
        if t - state[s.id] >= every * 60 - _JITTER_S:
            out.append(s.id)
    return out


def mark_ran(state, ids, now=None):
    # type: (Dict[str, float], Iterable[str], Optional[float]) -> Dict[str, float]
    t = time.time() if now is None else now
    for sid in ids:
        state[sid] = t
    return state
