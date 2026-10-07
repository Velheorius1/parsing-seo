"""Реестр топ-100 заказчиков полиграфии и мерча (фаза 3 плана 07.10.2026).

Файл — data/private/customer_registry.json: репозиторий публичный, список
заказчиков и связи с клиентами Битрикса в git не кладём. Пересборка — раз в
месяц из customer_rank (--registry). Ручные поля переживают пересборку:

  pinned        — закреплён Данияром: остаётся и вне топа, и без покупок;
  inns          — группа ИНН одной организации (структуры, филиалы); больше
                  одного ИНН — только после «да» (merged_by);
  bitrix        — подтверждённые компании Битрикса; ИНН у компаний в Битриксе
                  нет, сопоставление по имени, поэтому только после «да»;
  aliases, own_site_url, note, segment_override — ручные.

Предложения (merge_proposals, bitrix_proposals) ничего не меняют до «да».
Отклонённые хранятся, чтобы не предлагать то же самое каждый месяц.
Здесь только чистые функции: без базы, сети и настроек.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from crawler.core.competitor_audit import normalize_inn

REGISTRY_PATH = Path(__file__).resolve().parents[2] / 'data' / 'private' / 'customer_registry.json'
MANUAL_FIELDS = ('pinned', 'pinned_by', 'aliases', 'own_site_url', 'note', 'segment_override',
                 'bitrix', 'merged_by')
BITRIX_STALE_DAYS = 45


def empty():
    # type: () -> Dict[str, Any]
    return {'as_of': None, 'top': 0, 'entities': [], 'merge_proposals': [], 'bitrix_proposals': []}


def validate(registry):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Один ИНН — одна сущность; без имени и с битым ИНН реестр не грузим."""
    if not isinstance(registry, dict) or not isinstance(registry.get('entities'), list):
        raise ValueError('customer registry must be an object with an entities list')
    ids, seen = set(), {}  # type: (set, Dict[str, str])
    for entity in registry['entities']:
        name = entity.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('customer entity requires a name')
        if entity.get('id') in ids:
            raise ValueError('duplicate customer id %s' % entity.get('id'))
        ids.add(entity.get('id'))
        inns = []
        for raw in entity.get('inns') or []:
            inn = normalize_inn(raw)
            if inn is None:
                raise ValueError('invalid INN %r in %s' % (raw, name))
            if inn in seen:
                raise ValueError('INN %s is in both %s and %s' % (inn, seen[inn], entity['id']))
            seen[inn] = entity['id']
            inns.append(inn)
        if not inns:
            raise ValueError('customer entity %s has no INN' % name)
        entity['inns'] = inns
    for key in ('merge_proposals', 'bitrix_proposals'):
        registry.setdefault(key, [])
    return registry


def load(path=None):
    # type: (Optional[Path]) -> Dict[str, Any]
    path = Path(path or REGISTRY_PATH)
    if not path.exists():
        return empty()
    return validate(json.loads(path.read_text(encoding='utf-8')))


def save(registry, path=None):
    # type: (Dict[str, Any], Optional[Path]) -> Path
    path = Path(path or REGISTRY_PATH)
    validate(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)
    return path


def entity_id(inns):
    # type: (List[str]) -> str
    return 'c-%s' % inns[0]


def groups(registry):
    # type: (Dict[str, Any]) -> Dict[str, Dict[str, Any]]
    """ИНН → сущность для подтверждённых групп (больше одного ИНН): по ним
    customer_rank складывает траты структур в одну строку."""
    out = {}  # type: Dict[str, Dict[str, Any]]
    for entity in registry.get('entities') or []:
        if len(entity.get('inns') or []) > 1:
            for inn in entity['inns']:
                out[inn] = entity
    return out


def by_inn(registry):
    # type: (Dict[str, Any]) -> Dict[str, Dict[str, Any]]
    return {inn: entity for entity in registry.get('entities') or [] for inn in entity.get('inns') or []}


def build(ranked, previous, as_of, top=100):
    # type: (List[Dict[str, Any]], Dict[str, Any], str, int) -> Dict[str, Any]
    """Новый реестр из полного рейтинга (по убыванию балла).

    В реестр идут первые ``top`` и все закреплённые; у закреплённого без покупок
    траты нулевые, rank — None. Ручные поля и предложения переносятся как есть.
    """
    old_by_inn = by_inn(previous)
    entities, placed = [], set()  # type: (List[Dict[str, Any]], set)
    for position, row in enumerate(ranked, 1):
        inns = list(row.get('inns') or [row['inn']])
        old = next((old_by_inn[inn] for inn in inns if inn in old_by_inn), None)
        if position > top and not (old and old.get('pinned')):
            continue
        entity = {'id': old['id'] if old else entity_id(inns), 'name': row['name'], 'inns': inns,
                  'segment': row.get('segment'), 'rank': position, 'score': row.get('score'),
                  'spend12': row.get('spend12'), 'spend24': row.get('spend24'),
                  'purchases': row.get('purchases'), 'last': row.get('last')}
        if old:
            entity.update({key: old[key] for key in MANUAL_FIELDS if key in old})
            if old.get('merged_by'):
                entity['name'] = old['name']
        entities.append(entity)
        placed.add(entity['id'])
    for old in previous.get('entities') or []:
        if old.get('pinned') and old['id'] not in placed:
            kept = {key: old[key] for key in ('id', 'name', 'inns', 'segment') if key in old}
            kept.update({'rank': None, 'score': 0, 'spend12': 0, 'spend24': 0, 'purchases': 0, 'last': None})
            kept.update({key: old[key] for key in MANUAL_FIELDS if key in old})
            entities.append(kept)
    return validate({'as_of': as_of, 'top': top, 'entities': entities,
                     'merge_proposals': list(previous.get('merge_proposals') or []),
                     'bitrix_proposals': list(previous.get('bitrix_proposals') or []),
                     'bitrix_export': previous.get('bitrix_export')})


def _decision(by, day):
    # type: (str, str) -> str
    return '%s %s' % (by, day)


def merge(registry, inns, name, by, day):
    # type: (Dict[str, Any], Iterable[str], str, str, str) -> Dict[str, Any]
    """Подтверждённое объединение ИНН в одну организацию. Ручные поля частей
    сохраняются: закреплённость — если была хоть у одной, Битрикс — все."""
    wanted = [normalize_inn(inn) for inn in inns]
    if None in wanted or len(set(wanted)) < 2:
        raise ValueError('merge needs at least two valid distinct INNs')
    parts = [entity for entity in registry['entities'] if set(entity['inns']) & set(wanted)]
    keep = [entity for entity in registry['entities'] if entity not in parts]
    all_inns = []  # type: List[str]
    for inn in wanted + [inn for part in parts for inn in part['inns']]:
        if inn not in all_inns:
            all_inns.append(inn)
    merged = {'id': next((part['id'] for part in parts if part['inns'][0] == all_inns[0]), entity_id(all_inns)),
              'name': name, 'inns': all_inns, 'merged_by': _decision(by, day)}
    for key in ('segment', 'rank', 'score', 'spend12', 'spend24', 'purchases', 'last'):
        if parts:
            merged[key] = parts[0].get(key)
    if any(part.get('pinned') for part in parts):
        merged['pinned'], merged['pinned_by'] = True, next(p.get('pinned_by') for p in parts if p.get('pinned'))
    bitrix = [link for part in parts for link in part.get('bitrix') or []]
    if bitrix:
        merged['bitrix'] = bitrix
    registry['entities'] = keep + [merged]
    for proposal in registry.get('merge_proposals') or []:
        if set(proposal.get('inns') or []) <= set(all_inns) and proposal.get('status') == 'proposed':
            proposal['status'], proposal['decided'] = 'confirmed', _decision(by, day)
    return validate(registry)


def link_bitrix(registry, company, inn, by, day):
    # type: (Dict[str, Any], Dict[str, Any], str, str, str) -> Dict[str, Any]
    """Подтверждённая связь компании Битрикса с заказчиком (по ИНН сущности)."""
    entity = by_inn(registry).get(normalize_inn(inn) or '')
    if entity is None:
        raise ValueError('INN %s is not in the customer registry' % inn)
    links = entity.setdefault('bitrix', [])
    if all(str(link['id']) != str(company['id']) for link in links):
        links.append({'id': company['id'], 'name': company['name'], 'confirmed': _decision(by, day)})
    for proposal in registry.get('bitrix_proposals') or []:
        if str(proposal.get('bitrix_id')) == str(company['id']) and proposal.get('inn') in entity['inns']:
            proposal['status'], proposal['decided'] = 'confirmed', _decision(by, day)
    return registry


def reject(registry, kind, key, by, day):
    # type: (Dict[str, Any], str, str, str, str) -> int
    """Отклонить предложение: kind 'merge' (key — ИНН через запятую) или
    'bitrix' (key — «id_битрикс:ИНН»). Отклонённое больше не предлагается."""
    count = 0
    for proposal in registry.get('%s_proposals' % kind) or []:
        own = ','.join(proposal.get('inns') or []) if kind == 'merge' else \
            '%s:%s' % (proposal.get('bitrix_id'), proposal.get('inn'))
        if own == key and proposal.get('status') == 'proposed':
            proposal['status'], proposal['decided'] = 'rejected', _decision(by, day)
            count += 1
    return count


def pin(registry, inn, name, by, day):
    # type: (Dict[str, Any], str, str, str, str) -> Dict[str, Any]
    """Закрепить заказчика; вне топа — завести сущность с нулевыми тратами."""
    wanted = normalize_inn(inn)
    if wanted is None:
        raise ValueError('invalid INN %r' % inn)
    entity = by_inn(registry).get(wanted)
    if entity is None:
        entity = {'id': entity_id([wanted]), 'name': name, 'inns': [wanted], 'rank': None, 'score': 0,
                  'spend12': 0, 'spend24': 0, 'purchases': 0, 'last': None}
        registry['entities'].append(entity)
    entity['pinned'], entity['pinned_by'] = True, _decision(by, day)
    return validate(registry)


def add_proposal(registry, kind, proposal):
    # type: (Dict[str, Any], str, Dict[str, Any]) -> bool
    """Добавить предложение, если такого (в любом статусе) ещё не было."""
    key = 'inns' if kind == 'merge' else 'bitrix_id'
    bucket = registry.setdefault('%s_proposals' % kind, [])
    for old in bucket:
        same = sorted(old.get('inns') or []) == sorted(proposal.get('inns') or []) if kind == 'merge' else \
            (str(old.get(key)) == str(proposal.get(key)) and old.get('inn') == proposal.get('inn'))
        if same:
            return False
    bucket.append(dict(proposal, status='proposed'))
    return True


# ── Предложения связей с Битриксом: сходство названий ────────────────────────
# Только предложения: решает Данияр. Названия в Битриксе бытовые («NBU Bank»,
# «aloqabank»), у компаний нет ИНН — поэтому сравниваем значимые слова после
# транслитерации, а то, чего сходство не видит, добавляется предложением руками.

_CYR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ',
                ['a', 'b', 'v', 'g', 'd', 'e', 'yo', 'j', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p',
                 'r', 's', 't', 'u', 'f', 'h', 'ts', 'ch', 'sh', 'sh', '', 'i', '', 'e', 'yu', 'ya',
                 'o', 'k', 'g', 'h']))
_LEGAL = frozenset(('aj', 'atb', 'atib', 'mchj', 'mchzh', 'ooo', 'ao', 'jsc', 'llc', 'dm', 'duk', 'uk', 'xk',
                    'hk', 'kk', 'qk', 'gup', 'dk', 'chp', 'ytt', 'yatt', 'sp', 'at', 'ruz', 'ozr',
                    'aksiyadorlik', 'aktsionernoe', 'obshchestvo', 'jamiyati', 'tijorat', 'davlat',
                    'unitar', 'korhonasi', 'respublikasi', 'respubliki', 'ozbekiston', 'uzbekistan',
                    'uzbekiston', 'huzuridagi', 'boshkarmasi', 'filiali', 'filial', 'mark', 'amal', 'boshk'))
# Слово, которое одно ничего не значит: «bank» совпал бы со всеми банками.
_GENERIC = frozenset(('bank', 'banki', 'banka', 'group', 'grupp', 'company', 'kompaniya', 'servis', 'service',
                      'trade', 'house', 'home', 'center', 'tsentr', 'markaz', 'markazi', 'fond', 'plus',
                      'market', 'shop', 'studio', 'lab', 'pro', 'invest', 'holding', 'zavod', 'zavodi',
                      'vazirligi', 'agentligi', 'agentstvo', 'ministerstvo', 'test', 'admin', 'klient',
                      'chastnoe', 'litso', 'super', 'sport', 'auto', 'avto', 'city', 'siti', 'shahar',
                      'shahri', 'tuman', 'tumani', 'viloyat', 'viloyati', 'oblast',
                      # Город в названии («Самарканд Регенси», «UVD SAMARQAND») — не та же организация.
                      'tashkent', 'toshkent', 'samarkand', 'samarqand', 'buhara', 'buhoro', 'andijan', 'andijon',
                      'fergana', 'fargona', 'namangan', 'navoi', 'navoiy', 'karshi', 'karshi', 'termez',
                      'termiz', 'nukus', 'horezm', 'horazm', 'jizzah', 'jizzax', 'sirdaryo', 'kashkadaryo',
                      'surhondaryo', 'karakalpakstan', 'koraqalpogiston', 'korakalpogiston', 'angren',
                      'chirchik', 'olmalik', 'almalyk', 'kokand', 'kokon', 'gulistan', 'guliston'))


def name_tokens(name):
    # type: (Any) -> List[str]
    text = str(name or '').casefold()
    text = ''.join(_CYR.get(ch, ch) for ch in text)
    text = re.sub(r"[ʻʼ’‘`'´]", '', text).replace('q', 'k').replace('x', 'h')
    return [token for token in re.split(r'[^a-z0-9]+', text) if len(token) >= 3 and token not in _LEGAL]


def bitrix_candidates(registry, companies):
    # type: (Dict[str, Any], List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Пары «компания Битрикса — заказчик реестра» по общему значимому слову
    (≥4 букв, не общее вроде «bank»). Только кандидаты — не решение."""
    out = []
    for entity in registry.get('entities') or []:
        mine = set(name_tokens(entity['name']))
        for alias in entity.get('aliases') or []:
            mine.update(name_tokens(alias))
        mine = {token for token in mine if len(token) >= 4 and token not in _GENERIC}
        for company in companies:
            theirs = {token for token in name_tokens(company['name']) if len(token) >= 4 and token not in _GENERIC}
            common = sorted(mine & theirs)
            if common:
                out.append({'bitrix_id': company['id'], 'bitrix_name': company['name'], 'inn': entity['inns'][0],
                            'entity_name': entity['name'], 'why': 'общее слово: %s' % ', '.join(common)})
    return out
