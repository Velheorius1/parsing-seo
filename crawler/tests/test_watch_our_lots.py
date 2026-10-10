"""Сторож наших лотов не выжигает лимит площадки (10.10.2026).

ЧТО БЫЛО. Cron */2 и два запроса на тик — 1 440 запросов в сутки к бэкенду,
который с 07.10 пускает наш IP раз в минуту. Позиций Winch в э-магазине при
этом нет (состояние {"ads": {}}), то есть сторож весь лимит тратил впустую.
Второй запрос (встречные аукционы) шёл с limit=200 и с июля получал HTTP 400 —
сопоставление аукционов с нашим товаром не работало ни разу. А сбой запроса
читался как пустой ответ: «наша позиция ИСЧЕЗЛА», «аукцион завершён».

Здесь держится: без наших позиций — один запрос и следующая проверка через
30 мин; до срока — ни одного запроса; сбой — не «пусто»; limit ≤ 100.

Run: python3 -m crawler.tests.test_watch_our_lots   (exit 1 on any failure)
"""
import asyncio
import sys
import time
import types

from crawler.tests._stubs import install_settings_stub, install_stub

install_settings_stub()


class _Store(object):
    def __init__(self, state=None):
        self.state = state
        self.saved = []

    def get_setting(self, key):
        return self.state

    def set_setting(self, key, value):
        self.saved.append(value)
        return True


_STORE = _Store()
install_stub("crawler.auth.session_store", session_store=_STORE)

import crawler.scripts.watch_our_lots as W  # noqa: E402


def _run(state, replies):
    """replies: ref -> (ok, result). Возвращает (вызовы, отправленные алерты, store)."""
    store = _Store(state)
    calls, sent = [], []

    async def fake_rpc(client, method, params, path="/rpc"):
        calls.append(params)
        return replies[params["ref"]]

    async def fake_send(text):
        sent.append(text)
        return True

    mod = sys.modules["crawler.auth.session_store"]
    old = (W._rpc, W._send_tg, mod.session_store)
    W._rpc, W._send_tg, mod.session_store = fake_rpc, fake_send, store
    try:
        asyncio.run(W.tick())
    finally:
        W._rpc, W._send_tg, mod.session_store = old
    return calls, sent, store


ADS = "ref_online_shop_public"
REDS = "ref_reduction_object_public"


def test_no_ads_means_one_request_and_half_hour_pause():
    t0 = time.time()
    calls, sent, store = _run({"ads": {}, "auctions": {}}, {ADS: (True, [])})
    assert [c["ref"] for c in calls] == [ADS]
    assert sent == []
    nxt = store.saved[-1]["next_check_at"]
    assert t0 + 29 * 60 <= nxt <= time.time() + 31 * 60


def test_not_due_yet_makes_no_requests():
    state = {"ads": {}, "auctions": {}, "next_check_at": time.time() + 600}
    calls, sent, store = _run(state, {})
    assert calls == [] and sent == [] and store.saved == []


def test_failed_ads_request_changes_nothing():
    state = {"ads": {"7628192": {"name": "Блокнот", "price": 1}}, "auctions": {}}
    calls, sent, store = _run(state, {ADS: (False, None)})
    assert sent == [], "сбой запроса прочитан как «позиция исчезла»"
    assert store.saved == []


def test_failed_reductions_do_not_close_watched_auctions():
    ad = {"id": 1, "product_name": "Блокнот А5 с логотипом", "price": 1000}
    state = {"ads": {"1": {"name": ad["product_name"], "price": 1000}},
             "auctions": {"55": {"ad": "1", "product": ad["product_name"], "price": 900}}}
    calls, sent, store = _run(state, {ADS: (True, [ad]), REDS: (False, None)})
    assert not any("завершён" in m for m in sent), sent
    saved = store.saved[-1]
    assert "55" in saved["auctions"]
    assert saved["next_check_at"] <= time.time() + 1  # повторить на следующем тике


def test_ads_without_auction_check_every_ten_minutes():
    ad = {"id": 1, "product_name": "Блокнот А5 с логотипом", "price": 1000}
    state = {"ads": {"1": {"name": ad["product_name"], "price": 1000}}, "auctions": {}}
    t0 = time.time()
    calls, sent, store = _run(state, {ADS: (True, [ad]), REDS: (True, [])})
    assert [c["ref"] for c in calls] == [ADS, REDS]
    nxt = store.saved[-1]["next_check_at"]
    assert t0 + 9 * 60 <= nxt <= time.time() + 11 * 60


def test_live_auction_on_our_product_is_checked_every_tick():
    ad = {"id": 1, "product_name": "Блокнот А5 с логотипом", "price": 1000}
    red = {"id": 55, "meta": {"good_maps": [{"name": "Блокнот А5 с логотипом Winch"}]},
           "last_price": 950, "part_count": 2, "remain_time": 1800}
    state = {"ads": {"1": {"name": ad["product_name"], "price": 1000}}, "auctions": {}}
    calls, sent, store = _run(state, {ADS: (True, [ad]), REDS: (True, [red])})
    assert any("ЗАПУЩЕН" in m for m in sent), sent
    assert store.saved[-1]["next_check_at"] <= time.time() + 1


def test_every_request_respects_platform_limit():
    ad = {"id": 1, "product_name": "Блокнот А5 с логотипом", "price": 1000}
    state = {"ads": {"1": {"name": ad["product_name"], "price": 1000}}, "auctions": {}}
    calls, _, _ = _run(state, {ADS: (True, [ad]), REDS: (True, [])})
    assert calls and all(c.get("limit", 0) <= 100 for c in calls), calls


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
