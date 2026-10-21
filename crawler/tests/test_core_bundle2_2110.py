"""Аудит 21.10: витрины Ebirja, порог цены, дедуп рубрик.

- «Ebirja Электронный/Национальный магазин» — витрина продавцов (цена за
  единицу, мин./макс. заказ, страна, фото, «в корзину», заказчика нет), но
  маркер «э-магазин» их не ловил: 14 алертов из 540 подряд, пуш возможен.
- Порог 5 млн резал чатовые лиды по «цене», вынутой из количества («визитки
  1000 шт» → 1 200 сум), и валютные лоты (5 000 USD < «5 млн» как число).
- Дедуп «заказчик + слова заголовка»: у источников, где заголовок — рубрика
  («Отбор», «Услуги издательские: Книги печатные»), разные закупки одного
  заказчика склеивались. Сумма в ключе — как у предквалификаций с 09.10.

Run: .venv/bin/python3 -m pytest crawler/tests/test_core_bundle2_2110.py -q
"""
from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.core.dedup import _logical_key  # noqa: E402
from crawler.core.models import RawTender  # noqa: E402


def _t(**kw):
    base = dict(id="x-1", external_id="1", title="Визитки 1000 шт", organization="", source="X",
                source_url="https://x")
    base.update(kw)
    return RawTender(**base)


def test_ebirja_shops_are_supplier_catalogs():
    for src in ("Ebirja Электронный магазин", "Ebirja Национальный магазин"):
        assert N._is_supplier_catalog(src), src
        assert N._is_high_signal(_t(source=src, price=900_000_000.0)) is False, src
        assert "ПОСТАВЩИКА" in N._source_note(src), src


def test_min_price_spares_tg_leads_and_foreign_currency():
    lead = _t(id="l", external_id="l", source="TG: PR Media Group (запросы клиентов)",
              message_type="customer_request", price=1200.0)
    usd = _t(id="u", external_id="u", source="UNDP", title="Printing of brochures", price=5000.0, currency="USD")
    low = _t(id="z", external_id="z", source="ETender UZEX", title="Печать бланков", price=1_000_000.0)
    r = N.prefilter([lead, usd, low], keywords=["визитк", "printing", "печать", "бланк"])
    by = {v.tender.external_id: v.dropped_at for v in r.verdicts}
    assert by["l"] != N.DropStage.MIN_PRICE and by["u"] != N.DropStage.MIN_PRICE, by
    assert by["z"] == N.DropStage.MIN_PRICE, by


def test_rubric_title_sources_keep_price_in_dedup_key():
    for src in ("Hayotbirja отбор", "Xarid Аукционы"):
        a = _logical_key(src, "МИД", "Отбор", 120_000_000, None)
        b = _logical_key(src, "МИД", "Отбор", 56_500_000, None)
        assert a != b, src
        assert a == _logical_key(src, "МИД", "Отбор", 120_000_000, None)  # перевыкладка склеивается
