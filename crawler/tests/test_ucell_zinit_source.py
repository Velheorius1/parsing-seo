"""Ucell (COSCOM) — портал закупок Zinit (tender.ucell.uz).

ИЗ ЧЕГО ВЫРОСЛО. Старая страница ucell.uz/ru/procurement отдаёт 308 на
/partners, источник стоял выключенным, и годовой «ПКО Сувенирная продукция»
(03–14.09.2026) прошёл мимо нас. Данияр 07.10.2026 спросил, увидим ли мы
годовые тендеры операторов. Портал отдаёт список публично, без входа.
"""
import sys
from pathlib import Path

from crawler.adapters.api import ApiAdapter
from crawler.core.runner import load_sources

CONFIG = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"

# Реальная запись GET /api/v1/requests/ (07.10.2026), обрезана до нужных полей.
ITEM = {
    "id": 2015, "code": "002-015", "name": "ПКО \"Сувенирная продукция\"", "status": 3,
    "published_at": "2026-09-03T10:30:47.853991+05:00",
    "proposal_acceptance_end_date": "2026-09-14T15:00:00+05:00",
    "company": {"id": 3, "name": "ООО «COSCOM»", "tin": "201788904"},
}


def _source():
    return [c for c in load_sources(str(CONFIG)) if c.id == "ucell"][0]


def test_ucell_reads_the_public_zinit_api():
    cfg = _source()
    assert cfg.enabled and cfg.adapter == "api"
    assert cfg.url == "https://tender.ucell.uz/api/v1/requests/"
    assert cfg.params.get("sorting") == "-published_at", "свежие первыми, иначе первая страница — 2024 год"


def test_ucell_item_becomes_a_tender_with_deadline_link_and_inn():
    adapter = ApiAdapter(_source())
    items = adapter._extract_items({"page_info": {"total_count": 1}, "items": [ITEM]})
    t = adapter._convert_item(items[0])
    assert t.external_id == "2015" and t.id == "ucell-2015"
    assert t.title == "ПКО \"Сувенирная продукция\"" and t.organization == "ООО «COSCOM»"
    assert t.deadline == "2026-09-14T15:00:00+05:00", "срок подачи, а не дата публикации"
    assert t.source_url == "https://tender.ucell.uz/proposal/2015"
    assert t.extra_info["ИНН заказчика"] == "201788904" and t.extra_info["Номер"] == "002-015"
    assert "Сувенирная продукция" in t.search_text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            print("FAIL", test.__name__, "-", repr(exc)[:200])
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
