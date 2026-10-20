"""OSCE: id лота — весь slug, а не первое число ссылки (20.10.2026).

ЧТО БЫЛО. HTML-адаптер по умолчанию берёт id из первого числа в ссылке. У OSCE
slug с хвостом «-0», «-4» («provision-vehicle-rental-services-0») давал id «0» и
«4»: следующий лот с тем же хвостом upsert'ился в старую строку, считался
старым и не алертил. Тот же класс, что у Uzbekistan Airways (PR #109).

Run: .venv/bin/python3 -m pytest crawler/tests/test_osce_ids.py -q
"""
import os

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters.html import HtmlAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
URL = "https://procurement.osce.org/tenders?f[]=source:OSCE+Project+Co-ordinator+in+Uzbekistan"
SLUGS = [
    "provision-vehicle-rental-services-0",
    "provision-services-open-data-training-webinars-and-hackathon-4",
    "rental-premises-osce-project-co-ordinator-uzbekistan",
    "printing-services-0",
]


def _items():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml"), encoding="utf-8"))
    cfg = [s for s in raw["sources"] if s["id"] == "osce-uz"][0]
    page = "<html><body>%s</body></html>" % "".join(
        '<article class="node"><a href="/tenders/%s">Lot %d</a><time>2026-11-0%dT12:00:00Z</time></article>'
        % (slug, i, i + 1) for i, slug in enumerate(SLUGS))
    return HtmlAdapter(SourceConfig(**cfg))._parse_page(page, URL)


def test_id_is_the_whole_slug():
    ids = [t.external_id for t in _items()]
    assert ids == SLUGS, ids


def test_lots_with_the_same_tail_do_not_collapse():
    ids = [t.external_id for t in _items()]
    assert len(set(ids)) == len(SLUGS), ids
    assert not [i for i in ids if i.isdigit()], ids


# ── срок: «Deadline», а не «Launch date» (20.10.2026) ────────────────────────

_FIXTURE = os.path.join(_REPO, "crawler", "tests", "fixtures", "osce_list_2026-10-20.html")


def _live():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml"), encoding="utf-8"))
    cfg = [s for s in raw["sources"] if s["id"] == "osce-uz"][0]
    return HtmlAdapter(SourceConfig(**cfg))._parse_page(open(_FIXTURE, encoding="utf-8").read(), URL)


def test_deadline_is_the_deadline_not_the_launch_date():
    by_id = dict((t.external_id, t.deadline) for t in _live())
    # «Launch date 29 September 2026», «Deadline 22 October 2026 - 13:00, Asia/Tashkent»
    assert by_id["provision-services-open-data-training-webinars-and-hackathon-4"] == "2026-10-22T11:00:00Z", by_id


def test_open_lot_is_not_expired_on_its_launch_week():
    from datetime import datetime
    from crawler.core.notifier import _is_deadline_expired
    t = [x for x in _live() if x.external_id.endswith("hackathon-4")][0]
    assert _is_deadline_expired(t, now=datetime(2026, 10, 20, 12, 0)) is False
    assert _is_deadline_expired(t, now=datetime(2026, 10, 22, 11, 30)) is True
