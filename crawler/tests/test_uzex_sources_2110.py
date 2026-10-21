"""UZEX: ссылки auctionX, окно предквалификаций, один сборщик (аудит 21.10).

ЧТО БЫЛО.
- Ссылка «UZEX Обратные аукционы» вела в СТАРУЮ систему аукционов, где те же
  номера — чужие лоты 2021 г. W32 решил, что маршрута для auctionX нет; 21.10
  /new-auction/detail/1549 открыл в браузере правильный лот на 984 млн.
- Предквалификации: окно 2×500 при максимуме 888 за неделю, список по сроку ↑ —
  при всплеске потолок резал самые новые лоты.
- `fetch_uzex_auctions.py` в обёртке прокси писал вторую копию каждого лота
  (`uzex-prq-N`), копии алертились повторно: 21 двойной алерт за 60 дней.

Run: .venv/bin/python3 -m pytest crawler/tests/test_uzex_sources_2110.py -q
"""
import os
import re

import yaml

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(sid):
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml"), encoding="utf-8"))
    return [s for s in raw["sources"] if s["id"] == sid][0]


def test_auctionx_links_open_the_new_auction_route():
    tpl = _src("uzex-auctions")["field_map"]["source_url_template"]
    assert tpl == "https://xarid.uzex.uz/new-auction/detail/{id}", tpl


def test_prequal_window_has_room_above_the_weekly_peak():
    pg = _src("uzex-prequest")["pagination"]
    assert pg["page_size"] * pg["max_pages"] >= 2 * 888, pg


def test_proxy_wrapper_does_not_run_the_duplicate_uzex_collector():
    sh = open(os.path.join(_REPO, "scripts", "run_proxy_fetch.sh"), encoding="utf-8").read()
    live = [ln for ln in sh.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in live if re.search(r"fetch_uzex_auctions", ln)], live
