"""⭐-полоса топ-100 заказчиков (фаза 6 плана 07.10.2026).

Лот заказчика из топ-100 идёт в AI-гейт и без ключевого слова; в алерте — ⭐,
имя заказчика из реестра и кто у него раньше выигрывал наше. Словарь режет лоты
по предмету, а у банка или министерства из топа предмет часто назван так, что
ни одно слово не совпадёт («Merchendayzing», «Esdalik qo'l soati»): там решает
AI, как и для всех остальных.

Узнаём заказчика двумя способами, оба точные:
  • ИНН в extra_info лота (предквалификации, ВМК-69, прямые закупки Xarid, Ucell);
  • имя заказчика, приведённое к ключу (customer_registry.name_tokens: латиница,
    без кавычек и правовых форм), совпало с именем, под которым этот ИНН уже
    покупал в журнале закупок. Лоты etender ИНН не несут — только имя, а
    журнал сделок etender пишет имя в том же виде. Ключ короче 8 знаков или
    общий для двух сущностей не используется: «лишняя ⭐» хуже, чем её нет.

Режим — файл data/private/vip_lane.json, а не .env и не crawler_settings: при
выключенной полосе send_alerts не делает ни одного лишнего запроса, и поток
логов краулера остаётся прежним байт в байт (на нём держится A/B-сверка). Нет
файла или индекса — полоса выключена.
  off    — как до фазы 6;
  shadow — лоты «только по ⭐» в AI не идут и не шлются, пишутся в
           logs/vip_shadow.jsonl (замер на живом потоке); ⭐ ставится на обычные алерты;
  digest — проходят AI и идут только в дайджест;
  push   — обычная маршрутизация.
Индекс и реестр — коммерческая информация: только data/private (gitignored),
репозиторий публичный.
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from crawler.core.customer_registry import name_tokens

PRIVATE_DIR = Path(__file__).resolve().parents[2] / 'data' / 'private'
INDEX_PATH = PRIVATE_DIR / 'vip_index.json'
MODE_PATH = PRIVATE_DIR / 'vip_lane.json'
SHADOW_LOG = Path(__file__).resolve().parents[2] / 'logs' / 'vip_shadow.jsonl'
MODES = ('off', 'shadow', 'digest', 'push')
MIN_ALIAS = 8
INN_KEYS = ('ИНН заказчика', 'customer_inn', 'buyer_inn')
KW_PREFIX = 'vip:'


def alias_key(name):
    # type: (Any) -> str
    return ' '.join(name_tokens(name))


def build_index(registry, ledger_rows, as_of, is_ours=None):
    # type: (Dict[str, Any], Iterable[Dict[str, Any]], str, Optional[Callable[[str], bool]]) -> Dict[str, Any]
    """Индекс из реестра и журнала закупок. Чистая функция.

    ledger_rows — строки журнала заказчиков реестра (buyer_inn, buyer_name,
    winner_name, amount_uzs, profile): имена дают ключи, покупки нашего профиля —
    «кто раньше выигрывал» (свои победы не в счёт).
    """
    inns, entities = {}, {}  # type: Dict[str, str], Dict[str, Dict[str, Any]]
    for e in registry.get('entities') or []:
        for inn in e.get('inns') or []:
            inns[str(inn)] = e['id']
        entities[e['id']] = {'name': e['name'], 'rank': e.get('rank'), 'segment': e.get('segment'), 'winners': []}
    keys = defaultdict(set)  # type: Dict[str, set]
    for e in registry.get('entities') or []:
        for name in [e['name']] + list(e.get('aliases') or []):
            keys[alias_key(name)].add(e['id'])
    wins = defaultdict(Counter)  # type: Dict[str, Counter]
    for row in ledger_rows:
        eid = inns.get(str(row.get('buyer_inn') or ''))
        if not eid:
            continue
        keys[alias_key(row.get('buyer_name'))].add(eid)
        winner = row.get('winner_name')
        if row.get('profile') in ('poly', 'merch') and winner and not (is_ours and is_ours(winner)):
            wins[eid][winner] += float(row.get('amount_uzs') or 0)
    aliases = dict((k, next(iter(v))) for k, v in keys.items() if len(k) >= MIN_ALIAS and len(v) == 1)
    for eid, counter in wins.items():
        entities[eid]['winners'] = [name for name, _ in counter.most_common(3)]
    return {'as_of': as_of, 'inns': inns, 'aliases': aliases, 'entities': entities}


def match(tender, index):
    # type: (Any, Optional[Dict[str, Any]]) -> Optional[str]
    """id сущности реестра, чей это лот, или None."""
    if not index:
        return None
    extra = getattr(tender, 'extra_info', None) or {}
    for key in INN_KEYS:
        inn = str(extra.get(key) or '').strip()
        if inn and inn in index['inns']:
            return index['inns'][inn]
    key = alias_key(getattr(tender, 'organization', None) or '')
    return index['aliases'].get(key) if len(key) >= MIN_ALIAS else None


def is_vip_kw(matched_kw):
    # type: (Optional[str]) -> bool
    return bool(matched_kw) and str(matched_kw).startswith(KW_PREFIX)


def star_line(entity):
    # type: (Dict[str, Any]) -> str
    """Строка под заголовком алерта (без Markdown — экранирует вызывающий)."""
    line = '⭐ Топ-100: %s (место %s)' % (entity.get('name') or '—', entity.get('rank') or '—')
    if entity.get('winners'):
        line += ' · раньше выигрывали: %s' % ', '.join(entity['winners'])
    return line


def mode(path=None):
    # type: (Optional[Path]) -> str
    """Режим полосы; нет файла, мусор или ошибка чтения — 'off'."""
    try:
        value = json.loads((path or MODE_PATH).read_text(encoding='utf-8')).get('mode')
    except Exception:
        return 'off'
    return value if value in MODES else 'off'


def load_index(path=None):
    # type: (Optional[Path]) -> Optional[Dict[str, Any]]
    try:
        index = json.loads((path or INDEX_PATH).read_text(encoding='utf-8'))
    except Exception:
        return None
    return index if index.get('inns') is not None and index.get('aliases') is not None else None


def set_mode(value, by, path=None):
    # type: (str, str, Optional[Path]) -> None
    if value not in MODES:
        raise ValueError('режим: %s' % ', '.join(MODES))
    target = path or MODE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps({'mode': value, 'set_by': by, 'set_at': datetime.now(timezone.utc).isoformat()},
                              ensure_ascii=False), encoding='utf-8')
    tmp.replace(target)


def log_shadow(items, path=None):
    # type: (Iterable[Dict[str, Any]], Optional[Path]) -> None
    """Лоты «только по ⭐» в режиме shadow — в журнал, не в Telegram. Ошибки глушатся:
    сбой замера не должен ронять отправку алертов."""
    try:
        target = path or SHADOW_LOG
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(str(target), 'a', encoding='utf-8') as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False, default=str) + '\n')
    except Exception:
        pass
