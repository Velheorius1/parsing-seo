#!/usr/bin/env python3
"""Разовый бэкфилл: победители ВМК-69 («UZEX Результаты») в виде «Имя (ИНН 123)».

Из чего выросло. results_tracker писал победителя либо именем (ИНН терялся),
либо «ИНН: 123» (имени нет); разбор побед конкурентов ищет «ИНН <цифры>». Новый
формат (`results_tracker.format_winner`) крон проставляет сам, но API отдаёт лишь
последние ~500 итогов, а строки в базе лежат с июня. Этот скрипт дотягивает
старые из той же GetResulted (в ней ~9300 итогов).

Меняется ТОЛЬКО колонка `winner` и только у строк, где она отличается от того,
что скажет API. Без --apply ничего не пишет и печатает счётчики и примеры.

Usage:
  python3 -m crawler.scripts.backfill_civil_winner_inn            # dry-run
  python3 -m crawler.scripts.backfill_civil_winner_inn --apply
  --max-pages N   потолок страниц GetResulted (по умолчанию 25 × 500 = 12 500)
"""
import argparse
import logging
import os
import sys
import time
from typing import Any, Dict, List, Tuple

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.core import results_tracker as RT  # noqa: E402
from crawler.core.competitor_wins import winner_inn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_civil_winner_inn")

PAGE = 500
SOURCE = RT._RESULTS_SOURCE


def fetch_api(max_pages, post=None, pause=1.0):
    # type: (int, Any, float) -> Tuple[Dict[str, str], int, int]
    """(external_id → winner, строк получено, total_count по версии API).

    Дубль id — последний. Полнота — забота вызывающего: получено < total_count
    значит часть итогов не видна, и --apply в таком случае отказывает.
    """
    out = {}  # type: Dict[str, str]
    raw, total = 0, 0
    own = None
    if post is None:
        own = httpx.Client(timeout=40)
        post = own.post
    try:
        for page in range(max_pages):
            start = page * PAGE
            resp = post(RT._UZEX_RESULTS_URL, json={"from": start, "to": start + PAGE - 1},
                        headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                raise RuntimeError("GetResulted: ждали список, пришло %s" % type(rows).__name__)
            if not rows:
                break
            raw += len(rows)
            try:
                total = max(total, int(rows[0].get("total_count") or 0))
            except (TypeError, ValueError):
                pass
            for item in rows:
                row = RT._build_result_row(item)
                if row and row.get("winner"):
                    out[row["external_id"]] = row["winner"]
            logger.info("API: страница %d → %d строк, накоплено %d", page + 1, len(rows), len(out))
            time.sleep(pause)
    finally:
        if own is not None:
            own.close()
    return out, raw, total


def fetch_db():
    # type: () -> List[Dict[str, Any]]
    """Строки источника: keyset по id (в PostgREST потолок 1000, offset глубже — 57014)."""
    from crawler.core.db import _get_client, query_with_retry
    c = _get_client()
    out, last = [], None  # type: List[Dict[str, Any]], Any
    while True:
        def build(last=last):
            q = c.table("tenders").select("id,external_id,winner").eq("source", SOURCE)
            if last:
                q = q.gt("id", last)
            return q.order("id").limit(500).execute()
        rows = query_with_retry(build, label="backfill-read").data or []
        out.extend(rows)
        if len(rows) < 500:
            return out
        last = rows[-1]["id"]


def plan(db_rows, api):
    # type: (List[Dict[str, Any]], Dict[str, str]) -> List[Dict[str, Any]]
    """Что менять: строка в базе, которой API даёт другого победителя.

    Не затираем ИНН: если в базе он уже есть, а в ответе API его нет (имя без
    ИНН, один адрес), строку оставляем — старого значения потом не вернуть.
    """
    todo = []
    for r in db_rows:
        new = api.get(r.get("external_id") or "")
        old = r.get("winner") or ""
        if not new or new == old:
            continue
        if winner_inn(old) and not winner_inn(new):
            continue
        todo.append({"id": r["id"], "old": r.get("winner"), "new": new})
    return todo


def main():
    # type: () -> int
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="записать (без флага — dry-run)")
    ap.add_argument("--max-pages", type=int, default=25)
    args = ap.parse_args()

    api, raw, total = fetch_api(args.max_pages)
    complete = bool(total) and raw >= total
    logger.info("API отдало %d из %d итогов%s", raw, total, "" if complete else " — ВЫБОРКА НЕПОЛНА")
    db_rows = fetch_db()
    todo = plan(db_rows, api)
    in_api = sum(1 for r in db_rows if r.get("external_id") in api)
    logger.info("в базе %d строк источника; API знает победителя у %d из них; к правке %d",
                len(db_rows), in_api, len(todo))
    for t in todo[:8]:
        logger.info("  %r → %r", t["old"], t["new"])
    if not args.apply:
        logger.info("dry-run: ничего не записано")
        return 0
    if not complete:
        logger.error("--apply отказано: API отдало не все итоги (%d из %d, потолок страниц %d) — "
                     "часть строк осталась бы в старом формате без пометки", raw, total, args.max_pages)
        return 2

    from crawler.core.db import _get_client, query_with_retry
    c = _get_client()
    done = 0
    for t in todo:
        def build(t=t):
            return c.table("tenders").update({"winner": t["new"]}).eq("id", t["id"]).execute()
        query_with_retry(build, label="backfill-write")
        done += 1
        if done % 200 == 0:
            logger.info("записано %d/%d", done, len(todo))
    logger.info("записано %d", done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
