"""ВМК-69: валюта лота берётся из API, а не подставляется «UZS» (24.09).

Без `currency` в field_map адаптер искал несуществующее поле и писал UZS
каждому лоту. Среди 286 открытых отборов 24.09 были один в долларах и один в
юанях — в алерте они выглядели бы суммой в сумах. Тест берёт настоящий конфиг
источника из sources.yaml, чтобы проверять то, что уедет на прод.
"""
import os

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters.api import ApiAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_YAML = os.path.join(os.path.dirname(__file__), "..", "config", "sources.yaml")


def _civil_adapter():
    with open(_YAML, encoding="utf-8") as handle:
        sources = yaml.safe_load(handle)["sources"]
    cfg = next(s for s in sources if s.get("id") == "etender-civil")
    cfg = dict(cfg, detail_fetch=None)
    return ApiAdapter(SourceConfig(**cfg))


def _item(currency_name):
    return {"id": 17811, "display_id": "26120500017811", "name": "Лот",
            "customer_name": "Заказчик", "cost": 12000.0,
            "currency_name": currency_name, "end_date": "2026-10-01"}


def test_foreign_currency_is_not_relabelled_as_uzs():
    assert _civil_adapter()._convert_item(_item("Доллар")).currency == "Доллар"
    assert _civil_adapter()._convert_item(_item("Юань")).currency == "Юань"


def test_sum_lot_keeps_the_api_name_like_etender_deals():
    assert _civil_adapter()._convert_item(_item("Сум")).currency == "Сум"
