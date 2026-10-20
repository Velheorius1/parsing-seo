"""Mobiuz: лента запросов информации (RFI) собирается отдельно (20.10.2026).

ЧТО БЫЛО. У Mobiuz две ленты: отборы (ОНП) на /ru/purchase/ — их собирает
источник `mobiuz`, — и RFI на /ru/purchase/rfi/. Вторую мы не собирали вовсе:
за 01.09–20.10 ни одного RFI в базе, мимо прошёл «Сувенирная продукция Welcome
box для сотрудников» (RFI 105763, 17–25.09) — наш профиль.

Здесь держится: карточки RFI читаются, id — номер лота (не год из ссылки),
дата карточки уходит в публикацию, а не в срок, и сувенирный RFI проходит словарь.

Run: .venv/bin/python3 -m pytest crawler/tests/test_mobiuz_rfi.py -q
"""
import os

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters.html import HtmlAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
PAGE = open(os.path.join(_HERE, "fixtures", "mobiuz_rfi_2026-10-20.html"), encoding="utf-8").read()
URL = "https://company.mobi.uz/ru/purchase/rfi/"


def _items(page=PAGE):
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml"), encoding="utf-8"))
    cfg = [s for s in raw["sources"] if s["id"] == "mobiuz-rfi"][0]
    return HtmlAdapter(SourceConfig(**cfg))._parse_page(page, URL)


def test_cards_are_read_with_lot_numbers_not_the_year():
    items = _items()
    ids = [t.external_id for t in items]
    assert len(items) == 20 and len(set(ids)) == 20, ids
    assert ids[0] == "106516" and "2026" not in ids, ids
    first = items[0]
    assert "Закупка устройств" in first.title, first.title
    assert first.source_url == "https://company.mobi.uz/ru/purchase/2026/106516/", first.source_url
    assert first.organization == "Mobiuz RFI", first.organization


def test_card_date_is_publication_not_deadline():
    for t in _items():
        assert t.deadline is None, (t.external_id, t.deadline)


def test_souvenir_rfi_passes_the_dictionary():
    from crawler.core.notifier import _find_matching_keyword
    page = PAGE.replace("Закупка устройств", "Сувенирная продукция «Welcome box» для сотрудников", 1)
    t = _items(page)[0]
    assert _find_matching_keyword(t, ["сувенир", "полиграф"]) == "сувенир"
