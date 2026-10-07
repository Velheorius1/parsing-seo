#!/usr/bin/env python3
"""Разметка журнала закупок: полиграфия / мерч / не наше (purchase_profile).

Берёт строки purchase_ledger без оценки, решает правилами, кандидатов — AI
пачками; один и тот же предмет (subject_hash) оценивается один раз за всё
время: прошлое решение из базы переиспользуется. Ручная метка (profile_src =
'human') не перезаписывается никогда. Каждое решение AI — строка в
data/purchase-raw/ai-decisions.jsonl.

  python3 -m crawler.scripts.purchase_classify                 # до 200 вызовов AI
  python3 -m crawler.scripts.purchase_classify --ai-calls 0    # только правила
  python3 -m crawler.scripts.purchase_classify --dry-run
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from crawler.core import purchase_profile as P
from crawler.scripts.purchase_backfill import RAW_DIR, TABLE, upsert_rows

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

_COLS = 'feed,business_id,subject,subject_codes,category,subject_hash,buyer_name,details_fetched_at'
BATCH = 25
# Эталон (разметка человеком, данные публичные) — заодно ручные метки: его
# решение не перебивает ни правило, ни AI.
GOLDEN = Path(__file__).resolve().parents[1] / 'benchmark' / 'purchase_golden_v1.json'


def human_labels(path=GOLDEN):
    # type: (Path) -> Dict[str, str]
    """{'h:<subject_hash>' | 'f:<family_key>': метка}: метка одного лота «Prezident
    sovgʻasi» закрывает и остальные лоты той же семьи."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    out = {}  # type: Dict[str, str]
    for item in data.get('labels') or []:
        if item.get('label') not in P.PROFILES:
            continue
        if item.get('subject_hash'):
            out['h:' + item['subject_hash']] = item['label']
        if item.get('subject'):
            out.setdefault('f:' + P.family_key(item['subject']), item['label'])
    return out


def _client():
    # type: () -> Any
    from crawler.core.db import _get_client
    return _get_client()


def unlabeled_rows(client, page=1000):
    # type: (Any, int) -> List[Dict[str, Any]]
    """Все строки без оценки, страницами по id (PostgREST отдаёт ≤1000)."""
    rows = []  # type: List[Dict[str, Any]]
    last_id = 0
    while True:
        batch = client.table(TABLE).select('id,' + _COLS).is_('profile', 'null').is_('deleted_at', 'null') \
            .gt('id', last_id).order('id').limit(page).execute().data or []
        rows.extend(batch)
        if len(batch) < page:
            return rows
        last_id = batch[-1]['id']


def known_hash_verdicts(client, hashes):
    # type: (Any, List[str]) -> Dict[str, str]
    """Уже принятые решения AI/человека по subject_hash (пачки по 50 — длинный in_ даёт 502)."""
    known = {}  # type: Dict[str, str]
    for start in range(0, len(hashes), 50):
        res = client.table(TABLE).select('subject_hash,profile,profile_src') \
            .in_('subject_hash', hashes[start:start + 50]).not_.is_('profile', 'null') \
            .in_('profile_src', ['ai', 'human']).execute().data or []
        for row in res:
            # Человек главнее AI при расхождении.
            if row['subject_hash'] not in known or row['profile_src'] == 'human':
                known[row['subject_hash']] = row['profile']
    return known


def plan(rows, human=None):
    # type: (List[Dict[str, Any]], Optional[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], int]
    """(решено человеком или правилами, кандидаты AI по hash, ждут деталей)."""
    decided = []  # type: List[Dict[str, Any]]
    candidates = {}  # type: Dict[str, List[Dict[str, Any]]]
    pending = 0
    human = human or {}
    for row in rows:
        label = human.get('h:%s' % row.get('subject_hash')) or \
            (human.get('f:' + P.family_key(row.get('subject'))) if row.get('subject') else None)
        if label:
            decided.append(dict(row, profile=label, profile_src='human'))
            continue
        profile, src = P.rule_verdict(row)
        if profile is not None:
            decided.append(dict(row, profile=profile, profile_src=src))
        elif src == 'ai' and row.get('subject_hash'):
            candidates.setdefault(row['subject_hash'], []).append(row)
        else:
            pending += 1
    return decided, candidates, pending


def _payload(row, now):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    return {'feed': row['feed'], 'business_id': row['business_id'], 'profile': row['profile'],
            'profile_src': row['profile_src'], 'profile_at': now}


def run(client, ai_calls, dry_run, call=None, log_path=None, human=None):
    # type: (Any, int, bool, Optional[Callable[[str], str]], Optional[Any], Optional[Dict[str, str]]) -> Dict[str, Any]
    rows = unlabeled_rows(client)
    decided, by_hash, pending = plan(rows, human)
    known = known_hash_verdicts(client, list(by_hash)) if by_hash else {}
    for subject_hash, group in by_hash.items():
        if subject_hash in known:
            decided.extend(dict(r, profile=known[subject_hash], profile_src='ai') for r in group)
    # Одна семья предметов (отличаются только цифрами) — один вызов и одно решение.
    candidates = {}  # type: Dict[str, List[Dict[str, Any]]]
    for subject_hash, group in by_hash.items():
        if subject_hash not in known:
            candidates.setdefault(P.family_key(group[0].get('subject')), []).extend(group)
    todo = list(candidates)
    model = ''
    calls = failed_calls = ai_decided = 0
    log = open(str(log_path), 'a', encoding='utf-8') if (log_path and not dry_run) else None
    try:
        for chunk in P.batches(todo, BATCH):
            if calls >= ai_calls:
                break
            items = [candidates[h][0] for h in chunk]
            calls += 1
            try:
                verdicts = P.classify_batch(items, call)
            except Exception as exc:
                failed_calls += 1
                logger.warning('AI batch failed: %s', str(exc)[:120])
                if failed_calls >= 3:
                    break
                continue
            for index, (profile, reason) in verdicts.items():
                family = chunk[index]
                ai_decided += 1
                decided.extend(dict(r, profile=profile, profile_src='ai') for r in candidates[family])
                if log:
                    log.write(P.decision_log_line(items[index].get('subject_hash'), items[index].get('subject'),
                                                  profile, reason, model or 'openrouter') + '\n')
            time.sleep(0.2)
    finally:
        if log:
            log.close()
    now = datetime.now(timezone.utc).isoformat()
    written = upsert_rows(client, [_payload(r, now) for r in decided], dry_run)
    by_src = {}  # type: Dict[str, int]
    for row in decided:
        key = '%s/%s' % (row['profile_src'], row['profile'])
        by_src[key] = by_src.get(key, 0) + 1
    return {'unlabeled': len(rows), 'written': written, 'pending_details': pending,
            'ai_candidates_hashes': len(by_hash), 'ai_reused': len(known), 'ai_families': len(candidates),
            'ai_calls': calls, 'ai_failed_calls': failed_calls, 'ai_decided_hashes': ai_decided,
            'ai_left_hashes': len(todo) - ai_decided, 'by_source': by_src}


def main():
    # type: () -> int
    parser = argparse.ArgumentParser(description='Разметка журнала закупок по профилю')
    parser.add_argument('--ai-calls', type=int, default=200, help='потолок вызовов AI (пачка = %d предметов)' % BATCH)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    result = run(_client(), args.ai_calls, args.dry_run, log_path=RAW_DIR / 'ai-decisions.jsonl',
                 human=human_labels())
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['ai_failed_calls'] >= 3 else 0


if __name__ == '__main__':
    sys.exit(main())
