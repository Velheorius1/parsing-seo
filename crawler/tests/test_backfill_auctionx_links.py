"""Бэкфилл ссылок auctionX: какие строки переписываются, какие нет (21.10.2026).

Run: .venv/bin/python3 -m pytest crawler/tests/test_backfill_auctionx_links.py -q
"""
from crawler.scripts.backfill_auctionx_links import new_url, plan


def test_both_old_routes_go_to_new_auction():
    assert new_url("1518", "https://xarid.uzex.uz/auction/detail/1518") == \
        "https://xarid.uzex.uz/new-auction/detail/1518"
    assert new_url("uzex-auc-1549", "https://xarid.uzex.uz/auction/detail/1549") == \
        "https://xarid.uzex.uz/new-auction/detail/1549"
    assert new_url("909", "https://new-xarid.uzex.uz/home/shop/detail/909?elektron=true") == \
        "https://xarid.uzex.uz/new-auction/detail/909"


def test_rows_left_alone():
    assert new_url("1549", "https://xarid.uzex.uz/new-auction/detail/1549") is None   # уже рабочая
    assert new_url("1518", "https://xarid.uzex.uz/auction/detail/1519") is None      # номер не совпал
    assert new_url("abc", "https://xarid.uzex.uz/auction/detail/1518") is None
    assert new_url(None, None) is None


def test_plan_keeps_ids():
    rows = [{"id": "a", "external_id": "502", "source_url": "https://new-xarid.uzex.uz/home/shop/detail/502?elektron=true"},
            {"id": "b", "external_id": "1549", "source_url": "https://xarid.uzex.uz/new-auction/detail/1549"}]
    assert plan(rows) == [{"id": "a", "old": rows[0]["source_url"],
                           "new": "https://xarid.uzex.uz/new-auction/detail/502"}]
