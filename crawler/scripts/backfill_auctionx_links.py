#!/usr/bin/env python3
"""Разовый бэкфилл: ссылки «UZEX Обратные аукционы» (auctionX) на рабочий маршрут.

Из чего выросло (аудит 21.10). Шаблон вёл на /auction/detail/{id} — UI старой
системы аукционов, где тот же номер — чужой лот 2021 года; до июня — на
new-xarid…/home/shop/detail/{id}. Рабочий маршрут /new-auction/detail/{id}
(шаблон исправлен 21.10). В базе 438 строк со старыми ссылками; сверка в браузере
21.10: 502, 909 и 1518 по новому маршруту открывают те же лоты, что в базе
(название и сумма совпали).

Меняется ТОЛЬКО `source_url` и только у строк, где номер в старой ссылке равен
номеру лота из external_id («1518» или «uzex-auc-1518»). Без --apply ничего не
пишет и печатает счётчики и примеры.

Usage:
  python3 -m crawler.scripts.backfill_auctionx_links            # dry-run
  python3 -m crawler.scripts.backfill_auctionx_links --apply
"""
import argparse
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_auctionx_links")

SOURCE = "UZEX Обратные аукционы"
NEW_URL = "https://xarid.uzex.uz/new-auction/detail/%s"
_OLD_URL = re.compile(r"^https://(?:xarid\.uzex\.uz/auction|new-xarid\.uzex\.uz/home/shop)/detail/(\d+)(?:\?elektron=true)?$")
_EXT_ID = re.compile(r"^(?:uzex-auc-)?(\d+)$")


def new_url(external_id, source_url):
    # type: (Optional[str], Optional[str]) -> Optional[str]
    """Новая ссылка или None, если строку трогать нельзя или незачем."""
    old = _OLD_URL.match(source_url or "")
    ext = _EXT_ID.match(external_id or "")
    if not old or not ext or old.group(1) != ext.group(1):
        return None
    return NEW_URL % ext.group(1)


def fetch_db():
    # type: () -> List[Dict[str, Any]]
    """Строки источника: keyset по id (в PostgREST потолок 1000)."""
    from crawler.core.db import _get_client, query_with_retry
    c = _get_client()
    out, last = [], None  # type: List[Dict[str, Any]], Any
    while True:
        def build(last=last):
            q = c.table("tenders").select("id,external_id,source_url").eq("source", SOURCE)
            if last:
                q = q.gt("id", last)
            return q.order("id").limit(500).execute()
        rows = query_with_retry(build, label="backfill-read").data or []
        out.extend(rows)
        if len(rows) < 500:
            return out
        last = rows[-1]["id"]


def plan(db_rows):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    todo = []
    for r in db_rows:
        url = new_url(r.get("external_id"), r.get("source_url"))
        if url:
            todo.append({"id": r["id"], "old": r.get("source_url"), "new": url})
    return todo


def main():
    # type: () -> int
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="записать (без флага — dry-run)")
    args = ap.parse_args()

    db_rows = fetch_db()
    todo = plan(db_rows)
    left = [r for r in db_rows if not (r.get("source_url") or "").startswith(NEW_URL % "")
            and r["id"] not in {t["id"] for t in todo}]
    logger.info("в базе %d строк источника; к правке %d; старых ссылок, которые не трогаем: %d",
                len(db_rows), len(todo), len(left))
    for t in todo[:5]:
        logger.info("  %s → %s", t["old"], t["new"])
    for r in left[:5]:
        logger.info("  не трогаем: %s %s", r.get("external_id"), r.get("source_url"))
    if not args.apply:
        logger.info("dry-run: ничего не записано")
        return 0

    from crawler.core.db import _get_client, query_with_retry
    c = _get_client()
    done = 0
    for t in todo:
        def build(t=t):
            return c.table("tenders").update({"source_url": t["new"]}).eq("id", t["id"]).execute()
        query_with_retry(build, label="backfill-write")
        done += 1
        if done % 200 == 0:
            logger.info("записано %d/%d", done, len(todo))
    logger.info("записано %d", done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
