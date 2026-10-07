#!/usr/bin/env python3
"""Правка реестра заказчиков: предложения и решения Данияра (фаза 3).

Реестр — data/private/customer_registry.json (core/customer_registry). Ничего
не подтверждается само: связи с Битриксом и объединения ИНН сначала ложатся
предложениями, решение записывается с именем и датой.

  # на Маке: из снимка Битрикса (Projects/winch/analytics/bitrix-database-*/snapshot)
  python3 -m crawler.scripts.customer_registry_edit bitrix-export --sqlite …/winch_bitrix.sqlite
  # на VPS:
  python3 -m crawler.scripts.customer_registry_edit bitrix-propose
  python3 -m crawler.scripts.customer_registry_edit bitrix-add --bitrix-id 61 --inn 200836354 --why "NBU = Нацбанк"
  python3 -m crawler.scripts.customer_registry_edit bitrix-confirm --bitrix-id 61 --inn 200836354
  python3 -m crawler.scripts.customer_registry_edit merge-propose --inns 200541739,207327301 --name "Минобороны" --why "…"
  python3 -m crawler.scripts.customer_registry_edit merge-confirm --inns 200541739,207327301 --name "Минобороны"
  python3 -m crawler.scripts.customer_registry_edit reject --kind bitrix --key 61:200836354
  python3 -m crawler.scripts.customer_registry_edit pin --inn 200836354 --name "Нацбанк"
  python3 -m crawler.scripts.customer_registry_edit show
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from crawler.core import customer_registry as CR
from crawler.scripts.customer_rank import BITRIX_PATH, load_bitrix

# Стадии воронок Битрикса Winch (снимок 07.10.2026). Заказ — карточка
# «Производства», кроме отменённой, и заявка с полученной оплатой.
_ORDER_SQL = ("(d.pipeline = 'Производство' AND d.stage <> 'Заказ отменен') OR "
              "(d.pipeline = 'Заявки' AND d.stage = 'Оплата получена')")


def bitrix_export(sqlite_path):
    # type: (Path) -> Dict[str, Any]
    """Компании снимка → минимум для сверки: id, имя, заказы, заявки, даты.
    Ни контактов, ни телефонов, ни сумм — на VPS едет только это."""
    con = sqlite3.connect('file:%s?mode=ro' % sqlite_path, uri=True)
    try:
        rows = con.execute(
            "SELECT c.id, c.name, SUM(CASE WHEN %s THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN d.pipeline = 'Заявки' THEN 1 ELSE 0 END), "
            "MAX(CASE WHEN %s THEN d.created_iso END), MAX(d.created_iso) "
            "FROM companies c JOIN deals d ON d.company_id = c.id GROUP BY c.id ORDER BY c.id" % (
                _ORDER_SQL, _ORDER_SQL)).fetchall()
    finally:
        con.close()
    manifest = Path(sqlite_path).with_name('manifest.json')
    try:
        as_of = json.loads(manifest.read_text(encoding='utf-8'))['captured_at_utc']
    except (OSError, ValueError, KeyError):
        as_of = datetime.fromtimestamp(Path(sqlite_path).stat().st_mtime, timezone.utc).isoformat()
    return {'as_of': as_of, 'source': Path(sqlite_path).parent.parent.name,
            'companies': [{'id': row[0], 'name': row[1], 'orders': row[2] or 0, 'requests': row[3] or 0,
                           'last_order': row[4], 'last_deal': row[5]} for row in rows]}


def _company(bitrix, bitrix_id):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    for company in (bitrix or {}).get('companies') or []:
        if str(company['id']) == str(bitrix_id):
            return company
    raise SystemExit('компании %s нет в выгрузке Битрикса' % bitrix_id)


def _inns(text):
    # type: (str) -> List[str]
    return [part.strip() for part in text.split(',') if part.strip()]


def show(registry):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return {'as_of': registry.get('as_of'), 'entities': len(registry['entities']),
            'pinned': [e['name'] for e in registry['entities'] if e.get('pinned')],
            'groups': [{'name': e['name'], 'inns': e['inns']} for e in registry['entities'] if len(e['inns']) > 1],
            'bitrix_linked': [{'name': e['name'], 'bitrix': [l['name'] for l in e['bitrix']]}
                              for e in registry['entities'] if e.get('bitrix')],
            'merge_proposals': [p for p in registry.get('merge_proposals') or [] if p.get('status') == 'proposed'],
            'bitrix_proposals': [p for p in registry.get('bitrix_proposals') or [] if p.get('status') == 'proposed']}


def main(argv=None):
    # type: (Any) -> int
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('bitrix-export')
    p.add_argument('--sqlite', required=True)
    p.add_argument('--out', default=str(BITRIX_PATH))
    sub.add_parser('bitrix-propose')
    for name in ('bitrix-add', 'bitrix-confirm'):
        p = sub.add_parser(name)
        p.add_argument('--bitrix-id', required=True)
        p.add_argument('--inn', required=True)
        p.add_argument('--why', default='')
    for name in ('merge-propose', 'merge-confirm'):
        p = sub.add_parser(name)
        p.add_argument('--inns', required=True)
        p.add_argument('--name', required=True)
        p.add_argument('--why', default='')
    p = sub.add_parser('reject')
    p.add_argument('--kind', choices=('merge', 'bitrix'), required=True)
    p.add_argument('--key', required=True)
    p = sub.add_parser('pin')
    p.add_argument('--inn', required=True)
    p.add_argument('--name', required=True)
    sub.add_parser('show')
    parser.add_argument('--by', default='Данияр', help='кто решил (пишется рядом с датой)')
    args = parser.parse_args(argv)
    day = date.today().isoformat()

    if args.cmd == 'bitrix-export':
        data = bitrix_export(Path(args.sqlite))
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps({'out': str(out), 'as_of': data['as_of'], 'companies': len(data['companies']),
                          'with_orders': sum(1 for c in data['companies'] if c['orders'])}, ensure_ascii=False))
        return 0

    registry = CR.load()
    if args.cmd == 'show':
        print(json.dumps(show(registry), ensure_ascii=False, indent=1))
        return 0
    bitrix = load_bitrix()
    if args.cmd == 'bitrix-propose':
        if bitrix is None:
            raise SystemExit('нет %s — сначала bitrix-export на Маке и scp' % BITRIX_PATH)
        added = [c for c in CR.bitrix_candidates(registry, bitrix['companies'])
                 if CR.add_proposal(registry, 'bitrix', dict(c, proposed='сходство имён %s' % day))]
        print(json.dumps(added, ensure_ascii=False, indent=1))
    elif args.cmd == 'bitrix-add':
        company = _company(bitrix, args.bitrix_id)
        entity = CR.by_inn(registry).get(args.inn)
        if entity is None:
            raise SystemExit('ИНН %s нет в реестре' % args.inn)
        CR.add_proposal(registry, 'bitrix', {'bitrix_id': company['id'], 'bitrix_name': company['name'],
                                             'inn': args.inn, 'entity_name': entity['name'],
                                             'why': args.why, 'proposed': 'вручную %s' % day})
    elif args.cmd == 'bitrix-confirm':
        CR.link_bitrix(registry, _company(bitrix, args.bitrix_id), args.inn, args.by, day)
    elif args.cmd == 'merge-propose':
        CR.add_proposal(registry, 'merge', {'inns': _inns(args.inns), 'name': args.name, 'why': args.why,
                                            'proposed': day})
    elif args.cmd == 'merge-confirm':
        CR.merge(registry, _inns(args.inns), args.name, args.by, day)
    elif args.cmd == 'reject':
        if not CR.reject(registry, args.kind, args.key, args.by, day):
            raise SystemExit('открытого предложения %s %s нет' % (args.kind, args.key))
    elif args.cmd == 'pin':
        CR.pin(registry, args.inn, args.name, args.by, day)
    CR.save(registry)
    print(json.dumps(show(registry), ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
