"""HUMO (АО «НМПЦ»): тендеры из открытого RSS (21.10.2026, решение Данияра).

ИЗ ЧЕГО ВЫРОСЛО. Аудит 21.10: запись «новых площадок нет (27.07)» оказалась
артефактом промпта разведчика — он пускал только .uz и запрещал «сайты одной
организации». У HUMO открытый RSS: 10 тендеров за 30 дней; мимо прошли мерч для
Самаркандского марафона (450 шт., 05.10), брендирование pop-up зоны (08.10) и
новогодняя кампания (12.10).

Здесь держится: RSS читается (адрес из <link>, который html.parser считает
пустым тегом), номер — slug целиком, срок — со страницы тендера в двух
формулировках, с часом там, где он есть; мерч проходит словарь.

Run: .venv/bin/python3 -m pytest crawler/tests/test_humo_tenders.py -q
"""
import asyncio
import os
import re

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters.html import HtmlAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
RSS = open(os.path.join(_HERE, "fixtures", "humo_tenders_rss_2026-10-21.xml"), encoding="utf-8").read()
URL = "https://humocard.uz/ru/about/tender/rss/"


def _adapter():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml"), encoding="utf-8"))
    return HtmlAdapter(SourceConfig(**[s for s in raw["sources"] if s["id"] == "humo-tenders"][0]))


def test_feed_items_have_links_and_slug_ids():
    items = _adapter()._parse_page(RSS, URL)
    assert len(items) == 10, len(items)
    ids = [t.external_id for t in items]
    assert len(set(ids)) == 10 and all(not i.isdigit() for i in ids), ids
    merch = [t for t in items if "мерча" in t.title][0]
    assert merch.source_url.startswith("https://humocard.uz/ru/about/tender/tender-na-izgotovlenie"), merch.source_url
    assert merch.organization == "HUMO (АО «НМПЦ»)", merch.organization


def test_merch_passes_the_dictionary():
    from crawler.core.notifier import _find_matching_keyword
    merch = [t for t in _adapter()._parse_page(RSS, URL) if "мерча" in t.title][0]
    assert _find_matching_keyword(merch, ["брендир", "мерч", "сувенир"]) is not None, merch.title


def _detail_pages():
    """Страница тендера = его полный текст из RSS (yandex:full-text)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(re.sub(r"<(/?)link>", r"<\1rsslink>", RSS), "html.parser")
    pages = {}
    for it in soup.select("item"):
        full = it.select_one("yandex\\:full-text")
        pages[it.select_one("rsslink").get_text(strip=True)] = "<html><body>%s</body></html>" % (
            full.get_text() if full else "")
    return pages


def test_deadlines_from_tender_pages():
    a = _adapter()
    a.last_error = None
    pages = _detail_pages()

    async def fake_fetch(client, url):
        return RSS if url == URL else pages.get(url)

    a._fetch_page = fake_fetch
    items = asyncio.run(a._fetch_items())
    got = dict((t.title[:30], t.deadline) for t in items)
    assert got["Тендер на изготовление и поста"] == "09.10.2026", got
    popup = [v for k, v in got.items() if k.startswith("Предварительный квалификацион")][0]
    assert popup == "2026-10-12T18:00:00+05:00", popup
    assert sum(1 for t in items if t.deadline) == 10, [(t.title[:30], t.deadline) for t in items]
