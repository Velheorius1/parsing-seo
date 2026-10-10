"""JSON-RPC адаптер и общая очередь к бэкенду XT-Xarid (10.10.2026).

ЧТО БЫЛО. На 429 адаптер повторял запрос через 2 и 4 секунды, а на третьем
отказе бросал исключение. При лимите площадки «раз в минуту с IP» повторы были
обречены: 07-10.10 все до одного получили 429. Исключение роняло источник
целиком — вместе со страницами, уже собранными до отказа.

Здесь держится: каждая страница бэкенда XT берёт слот общей очереди; 429
отодвигает очередь и ждёт следующего слота, а не 2-4 с; исчерпав повторы,
адаптер отдаёт собранное и пишет ошибку прогона вместо исключения; источники
других площадок по-прежнему идут через ограничитель процесса.

Run: python3 -m crawler.tests.test_jsonrpc_budget   (exit 1 on any failure)
"""
import asyncio
import sys
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.adapters.jsonrpc as J  # noqa: E402
from crawler.adapters.jsonrpc import JsonRpcAdapter  # noqa: E402


class _Resp(object):
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)


class _Client(object):
    """Отдаёт заранее заданные ответы по порядку и считает запросы."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def post(self, *a, **kw):
        self.calls += 1
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Budget(object):
    def __init__(self, grant=True):
        self.grant = grant
        self.acquired = 0
        self.penalized = 0

    async def acquire(self, key, max_wait=None):
        self.acquired += 1
        return self.grant

    def penalize(self, key, now=None, state_dir=None):
        self.penalized += 1


def _cfg(url="https://api.xt-xarid.uz/rpc", max_pages=3):
    fm = types.SimpleNamespace(title="name", price="totalcost", external_id="id",
                               currency="currency", deadline="close_at",
                               organization="company_name", region="area",
                               source_url_template="https://xt-xarid.uz/procedure/{external_id}/core")
    pag = types.SimpleNamespace(page_size=2, max_pages=max_pages)
    return types.SimpleNamespace(
        name="XT-Xarid тендеры", id="xt-xarid-tender", url=url,
        rpc_ref="ref_tender_public", rpc_method="ref", id_prefix="xtx-tend",
        keywords_fields=["name"], field_map=fm, item_filter=None, headers={},
        timeout=15, rate_limit=2.0, pagination=pag)


def _page(*ids):
    return {"result": [{"id": i, "name": "Лот номер %d" % i, "status": "open",
                        "company_name": "Заказчик"} for i in ids]}


def _run(cfg, responses, budget):
    client = _Client(responses)
    old_client, old_hb = J.httpx.AsyncClient, J.host_budget
    J.httpx.AsyncClient = lambda *a, **kw: client
    J.host_budget = types.SimpleNamespace(
        backend_for=old_hb.backend_for, acquire=budget.acquire, penalize=budget.penalize)
    try:
        adapter = JsonRpcAdapter(cfg)
        adapter.last_error = None
        rate_calls = []

        async def _rl():
            rate_calls.append(1)

        adapter.rate_limit = _rl
        items = asyncio.run(adapter._fetch_items())
        return adapter, items, client, rate_calls
    finally:
        J.httpx.AsyncClient, J.host_budget = old_client, old_hb


def test_each_xt_page_takes_a_queue_slot():
    b = _Budget()
    _, items, client, rate_calls = _run(_cfg(), [_Resp(200, _page(1, 2)), _Resp(200, _page(3))], b)
    assert [t.external_id for t in items] == ["1", "2", "3"]
    assert b.acquired == 2 and client.calls == 2
    assert rate_calls == []  # старый ограничитель процесса для XT не используется


def test_429_waits_for_next_slot_and_succeeds():
    b = _Budget()
    _, items, client, _ = _run(_cfg(max_pages=1), [_Resp(429), _Resp(200, _page(1))], b)
    assert [t.external_id for t in items] == ["1"]
    assert b.penalized == 1
    assert b.acquired == 2  # страница + повтор, оба через очередь


def test_exhausted_429_keeps_collected_pages():
    b = _Budget()
    adapter, items, client, _ = _run(
        _cfg(), [_Resp(200, _page(1, 2)), _Resp(429), _Resp(429), _Resp(429)], b)
    assert [t.external_id for t in items] == ["1", "2"]
    assert adapter.last_error and "429" in adapter.last_error
    assert b.penalized == 3


def test_queue_too_long_stops_without_exception():
    b = _Budget(grant=False)
    adapter, items, client, _ = _run(_cfg(), [], b)
    assert items == [] and client.calls == 0
    assert adapter.last_error and "очередь" in adapter.last_error


def test_other_platforms_keep_process_rate_limiter():
    b = _Budget()
    cfg = _cfg(url="https://api.example.uz/rpc", max_pages=1)
    _, items, client, rate_calls = _run(cfg, [_Resp(200, _page(1))], b)
    assert [t.external_id for t in items] == ["1"]
    assert b.acquired == 0 and rate_calls == [1]


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
