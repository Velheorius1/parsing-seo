"""TenderWeek снова собирается после смены вёрстки (10.10.2026).

ЧТО БЫЛО. 476 строк до 03.07, потом каждый прогон «Fetched 0» без ошибки:
сайт перешёл на шаблон tenderweek_2026 — карточки div.tender-card вместо
div.short-item, ссылка стала пустым <a> поверх карточки. Контейнеров не
находилось вовсе, поэтому не срабатывал и сигнал «селекторы устарели» (он
ловит только карточки без заголовка). Три месяца пропусков — а на главной
10.10 висел наш профиль: «Сумки из спанбонда с нанесением логотипа».

Здесь держится: карточки, заголовок, заказчик, ссылка и id в прежнем виде
(tender-NNNNN → NNNNN, старые строки не задвоятся), срок — дата после
«Истекает», закрытый лот отсекается префильтром.

Run: .venv/bin/python3 -m pytest crawler/tests/test_tenderweek.py -q
"""
import os
import sys
import types
from datetime import datetime

import yaml

if "pydantic_settings" not in sys.modules:
    _stub = types.ModuleType("pydantic_settings")

    class _BaseSettings(object):
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    _stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = _stub

from crawler.adapters.html import HtmlAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
HOME = open(os.path.join(_HERE, "fixtures", "tenderweek_home_2026-10-10.html"), encoding="utf-8").read()
URL = "https://tenderweek.com/"


def _items():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml")))["sources"]
    cfg = [s for s in raw if s["id"] == "tenderweek"][0]
    adapter = HtmlAdapter(SourceConfig(**cfg))
    adapter.last_error = None
    items = adapter._parse_page(HOME, URL)
    return items, adapter


def test_all_cards_are_found_and_none_is_lost():
    items, adapter = _items()
    assert len(items) == 12
    assert adapter.last_error is None


def test_bags_with_logo_card_fields():
    items, _ = _items()
    t = [x for x in items if x.external_id == "36638"][0]
    assert t.title == "Сумки из спанбонда с нанесением логотипа"
    assert t.organization == "ISTIQBOLLI AVLOD РСИЦ"
    assert t.source_url == "https://tenderweek.com/tender-36638"
    assert t.id == "tenderweek-36638"


def test_deadline_is_the_expiry_date_not_publication():
    from crawler.core.notifier import _parse_deadline
    items, _ = _items()
    t = [x for x in items if x.external_id == "36644"][0]
    assert "Истекает" in t.deadline
    assert _parse_deadline(t.deadline) == datetime(2026, 10, 19)


def test_closed_lot_is_cut_by_prefilter():
    from crawler.core.notifier import _is_deadline_expired
    items, _ = _items()
    t = [x for x in items if x.external_id == "36644"][0]
    assert _is_deadline_expired(t, now=datetime(2026, 10, 10, 6, 0)) is False
    assert _is_deadline_expired(t, now=datetime(2026, 10, 21, 6, 0)) is True


def test_ids_keep_the_old_numeric_form():
    items, _ = _items()
    assert all(x.external_id.isdigit() and len(x.external_id) == 5 for x in items)
