"""Отборы ebirja — новый источник; позиции у активных аукционов ebirja (11.10.2026).

ЧТО СЛУЧИЛОСЬ. Объявлений отборов ebirja у нас не было вовсе — журнал закупок
видел только договоры: 6,5 млрд «Услуга по печатанию рекламной продукции»
(DASTURIY MAHSULOTLAR…, 08.07) прошли мимо. У аукционов лоты собирались, но без
позиций: «Kassa mahsulotlari» Асаки (#891) умер на словах, а внутри была
«Книга кассира» на 344 млн.

ЧТО ПРИШИВАЕМ. Конфиг — настоящий sources.yaml (то, что уедет на прод); ответы
площадки — урезанные живые ответы 11.10 (landing-lots, landing-lots-view,
auction/active, auction-view). Текст позиций собирается тем же разбором путей,
что у адаптера.

Run: .venv/bin/python3 -m pytest crawler/tests/test_ebirja_selection.py -q
"""
import os

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters.api import ApiAdapter, _resolve_wildcard_path  # noqa: E402
from crawler.core import notifier as N  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402
from crawler.tests.test_keyword_logotip import KW  # noqa: E402

_YAML = os.path.join(os.path.dirname(__file__), "..", "config", "sources.yaml")


def _cfg(source_id):
    with open(_YAML, encoding="utf-8") as handle:
        sources = yaml.safe_load(handle)["sources"]
    return next(s for s in sources if s.get("id") == source_id)


def _with_detail(cfg, item, detail):
    """Как адаптер: текст позиций по text_fields → item['_detail_text']."""
    parts = []
    for path in cfg["detail_fetch"]["text_fields"]:
        for v in _resolve_wildcard_path(detail, path):
            if v and str(v) not in parts:
                parts.append(str(v))
    return dict(item, _detail_text=" ".join(parts))


SELECTION_LIST = {"result": {"data": [{
    "id": 1092, "type": 2, "lot": "26521012016108", "title": "Asosiy vositalar sotib olish", "state": 50,
    "company": {"id": 77, "title": "DASTURIY MAHSULOTLAR VA AXBOROT TEXNOLOGIYALARI", "tin": "305122818",
                "region": {"title_ru": "город Ташкент"}},
    "total_price": 6496000000, "public_discussion_end_date": None,
    "begin_date": "2026-06-20 10:00:00", "end_date": "2026-06-30 10:00:00", "position_count": 2}],
    "meta": {"totalCount": 1}}, "errors": []}
SELECTION_VIEW = {"result": {"id": 1092, "products": [
    {"classifier": {"title_ru": "Услуга по печатанию рекламной продукции",
                    "title_uz": "Reklama mahsulotlarini chop etish xizmati", "code": "18.12.19.000-00002"},
     "description": "ПОЛИГРАФИЯ ХИЗМАТЛАРИ ВА МАҲСУЛОТЛАРИНИ ЛОЙИҲАЛАШ"}]}}


def test_selection_source_asks_for_live_lots_the_way_the_platform_accepts():
    cfg = _cfg("ebirja-selection")
    assert cfg["enabled"] is True
    assert cfg["url"] == "https://xarid-api.ebirja.uz/tender-v2/producer/landing-lots"
    assert sorted(cfg["params"]["type[]"]) == [1, 2, 3, 4, 5, 6], "без type площадка отвечает 422"
    assert cfg["params"]["state[]"] == [45, 50], "46 в фильтре — 422, его отдаёт фильтр 45"
    assert "_detail_text" in cfg["keywords_fields"]
    assert cfg["detail_fetch"]["url_template"].endswith("landing-lots-view?id={id}")


def test_selection_lot_is_keyed_by_procedure_number_and_caught_by_its_positions():
    cfg = _cfg("ebirja-selection")
    adapter = ApiAdapter(SourceConfig(**dict(cfg, detail_fetch=None)))
    items = adapter._extract_items(SELECTION_LIST)
    assert len(items) == 1
    bare = adapter._convert_item(items[0])
    assert N._find_matching_keyword(bare, KW) is None, "заголовок «Asosiy vositalar…» словарь не видит"
    t = adapter._convert_item(_with_detail(cfg, items[0], SELECTION_VIEW))
    assert t.external_id == "26521012016108", "тот же номер, что procedure_id договора"
    assert t.source_url == "https://ebirja.uz/ru/tender/1092"
    assert t.organization.startswith("DASTURIY") and t.price == 6496000000
    assert t.extra_info["ИНН заказчика"] == "305122818"
    assert N._find_matching_keyword(t, KW) is not None, "предмет — в позициях"


AUCTION_LIST = {"result": {"data": [{
    "id": 891, "lot": "26521007040822", "title": "Kassa mahsulotlari",
    "company": {"title": "DAVLAT AKSIYADORLIK TIJORAT BANKI ASAKA", "tin": "203366731"},
    "region": {"title_ru": "город Ташкент"}, "begin_date": "2026-09-23 10:00:00",
    "auction_end": "2026-09-28 17:06:34", "total_sum": 781516400}]}}
AUCTION_VIEW = {"result": {"id": 891, "auction_classifiers": [
    {"description": "40-ilovadagi kitob Plombir va klishelar hisobini yuritish",
     "classifier": {"title_ru": "Книга кассира", "title_uz": "Kassir kitobi",
                    "classifier_category": {"title_ru": "Журналы учета"}}}]}}


def test_active_auction_gets_its_positions_and_lot_number():
    cfg = _cfg("ebirja-auctions-active")
    assert cfg["detail_fetch"]["url_template"] == \
        "https://xarid-api.ebirja.uz/auction/auction/auction-view?id={id}"
    assert "_detail_text" in cfg["keywords_fields"]
    adapter = ApiAdapter(SourceConfig(**dict(cfg, detail_fetch=None)))
    item = adapter._extract_items(AUCTION_LIST)[0]
    assert N._find_matching_keyword(adapter._convert_item(item), KW) is None
    t = adapter._convert_item(_with_detail(cfg, item, AUCTION_VIEW))
    assert t.external_id == "891", "ключ прежний — уже собранные лоты не рождаются заново"
    assert t.extra_info["Номер лота"] == "26521007040822" and t.extra_info["ИНН заказчика"] == "203366731"
    assert N._find_matching_keyword(t, KW) is not None, "«Книга кассира» — в позициях"
