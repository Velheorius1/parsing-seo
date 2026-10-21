"""Cooperation э-магазин: рабочая ссылка на оферту и сумма лота (21.10.2026).

ЧТО БЫЛО. Лоты э-магазина пушатся с 21.10 (решение Данияра), но:
- ссылка `/supplier/eshop/ML…` вела на «Sahifa topilmadi» — маршрута /supplier
  в SPA больше нет; 05.08 рабочий `/e-shop/:id` отклонили, потому что клали
  номер лота, а маршрут ждёт id оферты;
- цены у лота не было вовсе: курсы на 1 человека за 1 млн и 30 тысяч пакетов
  шли в оценку одинаково, порог суммы их не различал;
- в каждом пуше Cooperation стояла «Поиск (с десктопа)» на тот же мёртвый /supplier.

Здесь держится: ссылка — /e-shop/{id оферты} (M1203477 → 203477; перед пушем id
берётся из самой оферты), сумма = цена оферты × количество, сбой обогащения —
лот без цены, как раньше; в пуше нет ссылок на /supplier.

Run: .venv/bin/python3 -m pytest crawler/tests/test_coop_eshop_link.py -q
"""
import os
import sys

import httpx

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import fetch_cooperation as fc  # noqa: E402

from crawler.core.snap import is_broken_spa  # noqa: E402

SRC = "Cooperation.uz Э-магазин лоты"


def _item(offer="M1203477", qty=4):
    # форма записи — cabinet.cooperation.uz/api/eshop/lots/active, живой лот 21.10
    return {"id": 19593, "lotNumber": "ML1019598", "offerNumber": offer,
            "productName": {"ru": "Услуга по повышению квалификации персонала аэропортов"},
            "enktCode": "85.31.11.000-00003", "quantity": qty, "measure": {"ru": "чел."},
            "startDate": "2026-10-21T15:58:39", "endDate": "2026-10-23T15:58:39"}


def _transform(items, monkeypatch):
    monkeypatch.setattr(fc, "_fetch_cabinet_paged", lambda url, label: items)
    return fc.fetch_and_transform_eshop_lots()


def test_offer_id_from_number():
    assert fc._eshop_offer_id("M1203477") == 203477
    assert fc._eshop_offer_id("M1163131") == 163131
    for bad in ("", None, "ML1019598", "O1919962", "M123"):
        assert fc._eshop_offer_id(bad) is None, bad


def test_row_links_the_offer_not_the_dead_supplier_route(monkeypatch):
    row = _transform([_item()], monkeypatch)[0]
    assert row["source_url"] == "https://new.cooperation.uz/e-shop/203477"
    assert row["extra_info"] == {"quantity": 4, "measure": "чел.", "offer": "M1203477"}


def test_row_without_offer_number_links_the_shop_not_a_dead_page(monkeypatch):
    row = _transform([_item(offer="")], monkeypatch)[0]
    assert row["source_url"] == "https://new.cooperation.uz/e-shop"
    assert "offer" not in row["extra_info"]


def test_all_rows_share_one_key_set_for_bulk_upsert(monkeypatch):
    """PostgREST требует одинаковые ключи у строк одного upsert."""
    rows = _transform([_item(), _item(offer="", qty="")], monkeypatch)
    assert set(rows[0]) == set(rows[1])


# ── обогащение перед пушем ──

def _enrich(row, handler, monkeypatch):
    saved = []
    real = httpx.Client
    monkeypatch.setattr(fc.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(fc, "_persist_fields", lambda r, fields: saved.append(fields))
    fc._enrich_eshop_row(row)
    return saved


def _row(monkeypatch):
    return _transform([_item()], monkeypatch)[0]


def test_enrich_sets_sum_and_offer_id_from_the_offer(monkeypatch):
    def handler(request):
        assert request.url.params["NameOrNumber"] == "M1203477"
        return httpx.Response(200, json={"data": [{"id": 203477, "offerNumber": "M1203477", "price": 1050000}]})
    row = _row(monkeypatch)
    saved = _enrich(row, handler, monkeypatch)
    assert row["price"] == 4 * 1050000.0
    assert row["extra_info"]["unit_price"] == 1050000
    assert row["source_url"] == "https://new.cooperation.uz/e-shop/203477"
    assert saved and saved[0]["price"] == 4200000.0


def test_enrich_prefers_the_real_offer_id(monkeypatch):
    """Формула номер→id сверена на 152 офертах, но перед пушем верим самой оферте."""
    row = _row(monkeypatch)
    _enrich(row, lambda r: httpx.Response(200, json={"data": [
        {"id": 999, "offerNumber": "M1203477", "price": 10}]}), monkeypatch)
    assert row["source_url"] == "https://new.cooperation.uz/e-shop/999"


def test_enrich_failure_leaves_row_as_before(monkeypatch):
    def boom(request):
        raise httpx.ConnectTimeout("t", request=request)
    for handler in (boom, lambda r: httpx.Response(500), lambda r: httpx.Response(200, json={"data": []})):
        row = _row(monkeypatch)
        before = dict(row, extra_info=dict(row["extra_info"]))
        assert _enrich(row, handler, monkeypatch) == []
        assert row == before


def test_enrich_ignores_other_sources(monkeypatch):
    row = dict(_row(monkeypatch), source="Cooperation.uz Лоты")
    assert _enrich(row, lambda r: 1 / 0, monkeypatch) == []


def test_eshop_alerts_are_enriched_before_the_shared_pipeline(monkeypatch):
    seen = []
    monkeypatch.setattr(fc, "_prefilter_rows", lambda rows, label: rows)
    monkeypatch.setattr(fc, "_enrich_eshop_row", lambda r: seen.append(r["external_id"]))
    monkeypatch.setattr(fc, "_row_to_raw_tender", lambda r: 1 / 0)  # дальше не идём
    monkeypatch.setattr(fc, "_notify_pipeline_failure", lambda *a: None)
    fc.send_alerts_unified([_row(monkeypatch)], "EshopLots")
    assert seen == ["coop-eshop-ML1019598"]


# ── ссылки в пуше ──

def test_eshop_link_is_a_working_route():
    assert not is_broken_spa(SRC)


def test_no_cooperation_push_links_the_dead_supplier_route():
    from crawler.core import notifier as N
    from crawler.core.models import RawTender
    for src, url in ((SRC, "https://new.cooperation.uz/e-shop/203477"),
                     ("Cooperation.uz Лоты", "https://new.cooperation.uz/supplier/lots?lotId=SL1")):
        t = RawTender(id="x", external_id="x", title="Пакеты полиэтиленовые", organization="O",
                      price=20_000_000.0, currency="UZS", source=src, source_url=url)
        txt = N._format_alert(t, "пакеты", alert_seq=1, db_id="U-1")
        assert "supplier/" not in txt, src
        assert "parsing-seo.vercel.app/tenders/U-1" in txt
    assert "Площадка (с десктопа): https://new.cooperation.uz/e-shop/203477" in N._format_alert(
        RawTender(id="x", external_id="x", title="Пакеты", organization="O", price=None, currency="UZS",
                  source=SRC, source_url="https://new.cooperation.uz/e-shop/203477"),
        "пакеты", alert_seq=2, db_id="U-2")
