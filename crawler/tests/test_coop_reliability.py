"""Cooperation: полный снимок разделов, повторы, сбой виден (21.10.2026).

ЧТО БЫЛО (аудит 21.10).
- cabinet-API (э-магазин, аукционы) МОЛЧА игнорирует skip/take и отдаёт первые
  10: э-магазин собирался на 10 из 302 живых лотов. Пагинация там — pageIndex/
  pageSize (до 50), итог — pagination.totalItems / totalCount.
- Каждый запрос — одна попытка; на ошибке раздел выходил из цикла, «Got 0»
  проходил тихо, прогон завершался с кодом 0. За 60 дней 60 из 181 прогона не
  довели лоты до базы, а сторожа молчали.
- Итоговая строка «new ~1100» считала хвост ещё не алертнутых, а не новые.

Здесь держится: раздел листается до конца и сверяется с итогом площадки;
временный отказ переживается повтором; стойкий отказ или неполный снимок —
статус раздела и код выхода 1; событие прогона пишется в журнал; «new» — только
впервые записанные строки.

Run: .venv/bin/python3 -m pytest crawler/tests/test_coop_reliability.py -q
"""
import json
import os
import sys
import tempfile
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import fetch_cooperation as fc  # noqa: E402


def _eshop_item(i):
    # форма записи — ответ cabinet.cooperation.uz/api/eshop/lots/active 21.10
    return {"id": i, "lotNumber": "ML%d" % (1000005 + i), "productName": {"ru": "Бумага офисная А4 %d" % i},
            "enktCode": "17.12.14", "quantity": 100, "measure": {"ru": "пачка"}, "offerNumber": "M123",
            "startDate": "2026-10-21T09:00:00", "endDate": "2026-10-23T09:00:00"}


class _Resp(object):
    def __init__(self, data, code=200):
        self.data, self.status_code = data, code

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError("HTTP %d" % self.status_code)

    def json(self):
        return self.data


class _Cabinet(object):
    """Ведёт себя как настоящий cabinet-API: skip/take игнорирует (первые 10),
    pageIndex/pageSize листает (не больше 50). fail — сколько первых вызовов отказать."""

    calls = []

    def __init__(self, items, fail=0, total_key="totalItems", total=None):
        self.items, self.fail, self.total_key = items, fail, total_key
        self.total = len(items) if total is None else total

    def __call__(self, **_kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get(self, url, params=None, headers=None):
        _Cabinet.calls.append(dict(params or {}))
        if self.fail > 0:
            self.fail -= 1
            return _Resp(None, 504)
        if "pageIndex" not in (params or {}):
            return _Resp({"code": 200, "data": self.items[:10]})
        size = min(int(params["pageSize"]), 50)
        page = int(params["pageIndex"])
        chunk = self.items[(page - 1) * size:page * size]
        pages = (len(self.items) + size - 1) // size
        return _Resp({"code": 200, "data": chunk, "pagination": {
            "currentPage": page, "totalPages": pages, self.total_key: self.total,
            "hasPrevious": page > 1, "hasNext": page < pages}})


def _reset():
    fc._RUN.clear()
    _Cabinet.calls = []


def test_eshop_takes_every_page_not_the_first_ten():
    _reset()
    api = _Cabinet([_eshop_item(i) for i in range(1, 303)])
    with patch.object(fc.httpx, "Client", api), patch.object(fc.time, "sleep"):
        rows = fc.fetch_and_transform_eshop_lots()
    assert len(rows) == 302, len(rows)
    assert [c["pageIndex"] for c in _Cabinet.calls] == list(range(1, 8)), _Cabinet.calls
    assert fc._RUN["EshopLots"]["status"] == "ok" and fc._RUN["EshopLots"]["total"] == 302, fc._RUN


def test_auction_total_comes_from_total_count():
    _reset()
    lots = [{"id": i, "lotNumber": "AL%d" % i, "name": {"ru": "Пакеты с логотипом"}, "companyName": {"ru": "АО"},
             "startPrice": 1e6, "endDate": "2026-10-28T10:00:00", "beginDate": "2026-10-21T10:00:00"}
            for i in range(1, 15)]
    with patch.object(fc.httpx, "Client", _Cabinet(lots, total_key="totalCount")), patch.object(fc.time, "sleep"):
        rows = fc.fetch_and_transform_auction_lots()
    assert len(rows) == 14 and fc._RUN["AuctionLots"]["status"] == "ok", (len(rows), fc._RUN)


def test_temporary_failure_is_survived_by_retry():
    _reset()
    api = _Cabinet([_eshop_item(i) for i in range(1, 31)], fail=2)
    with patch.object(fc.httpx, "Client", api), patch.object(fc.time, "sleep") as slept:
        rows = fc.fetch_and_transform_eshop_lots()
    assert len(rows) == 30 and fc._RUN["EshopLots"]["status"] == "ok", fc._RUN
    assert slept.call_count == 2, slept.call_args_list


def test_persistent_failure_is_a_failed_section_not_a_quiet_zero():
    _reset()
    api = _Cabinet([_eshop_item(i) for i in range(1, 31)], fail=99)
    with patch.object(fc.httpx, "Client", api), patch.object(fc.time, "sleep"):
        rows = fc.fetch_and_transform_eshop_lots()
    assert rows == [] and fc._RUN["EshopLots"]["status"] == "failed", fc._RUN
    assert "504" in fc._RUN["EshopLots"]["error"], fc._RUN


def test_snapshot_short_of_total_is_partial():
    _reset()
    api = _Cabinet([_eshop_item(i) for i in range(1, 31)], total=35)  # площадка говорит 35, отдаёт 30
    with patch.object(fc.httpx, "Client", api), patch.object(fc.time, "sleep"):
        rows = fc.fetch_and_transform_eshop_lots()
    assert len(rows) == 30 and fc._RUN["EshopLots"]["status"] == "partial", fc._RUN
    assert sum(1 for c in _Cabinet.calls if c.get("pageIndex") == 1) == 2  # перечитали один раз


def test_new_counts_only_first_time_written_rows():
    _reset()
    rows = [{"external_id": "coop-lot-SL%d" % i, "source": "Cooperation.uz Лоты", "title": "Книга %d" % i}
            for i in range(5)]
    keys = set((r["external_id"], r["source"]) for r in rows)
    with patch.object(fc, "get_existing_ids", return_value={"coop-lot-SL0", "coop-lot-SL1", "coop-lot-SL2", "coop-lot-SL3"}), \
            patch.object(fc, "get_alerted_ids", return_value=set()), \
            patch.object(fc, "upsert_rows", return_value=keys), \
            patch.object(fc, "send_alerts_unified", return_value=0), \
            patch.object(fc, "send_competitor_alerts", return_value=0):
        upserted, new, alerts, _, _ = fc.process_source(rows, "Cooperation.uz Лоты", "Lots", gate_on_alerted=True)
    assert (upserted, new) == (5, 1), (upserted, new)  # 5 неалертнутых кандидатов, новая — одна
    assert fc._RUN["Lots"]["candidates"] == 5 and fc._RUN["Lots"]["new"] == 1, fc._RUN


def test_bad_section_gives_exit_code_and_run_event():
    _reset()
    fc._section("Lots").update(fetched=1191, total=1191, source="Cooperation.uz Лоты")
    fc._mark("EshopLots", "partial", "took 10 of 302")
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        with patch.object(fc, "_RUNS_LOG", path):
            code = fc._finish()
        events = [json.loads(ln) for ln in open(path, encoding="utf-8")]
    finally:
        os.unlink(path)
    assert code == 1, code
    by = {e["section"]: e for e in events}
    assert by["Lots"]["status"] == "ok" and by["Lots"]["fetched"] == 1191, by
    assert by["EshopLots"]["status"] == "partial" and by["EshopLots"]["source"] == "Cooperation.uz Э-магазин лоты", by


def test_all_sections_ok_exit_zero():
    _reset()
    fc._section("Lots").update(fetched=10, total=10)
    with patch.object(fc, "_RUNS_LOG", os.devnull):
        assert fc._finish() == 0
