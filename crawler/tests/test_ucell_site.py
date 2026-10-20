"""Ucell: запросы с короткими номерами со страницы поставщиков (19.10.2026).

ЧТО БЫЛО. У Ucell два потока заявок. ПКО — на портале Zinit (источник `ucell`).
Мелкие запросы (№117–121: «Новогоднее оформление фасада» до 13.10, перевозка
оборудования, курсы) публикуются только карточками на ucell.uz/ru/partners с
архивом документов — на портале их нет, и мы их не видели вовсе.

Здесь держится: берутся только собственные карточки страницы (дубли портала
отсекает контейнер), название и срок читаются, id стабилен и не схлопывает
разные запросы — в том числе не берётся из ссылки на архив («cw-corn00…» дал
бы «00», а имена архивов вроде «transfer.7z» повторяются).

Run: python3 -m crawler.tests.test_ucell_site   (exit 1 on any failure)
"""
import os
import sys
import types

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
PAGE = open(os.path.join(_HERE, "fixtures", "ucell_partners_2026-10-19.html"), encoding="utf-8").read()
URL = "https://ucell.uz/ru/partners"


def _cfg():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml")))["sources"]
    return [s for s in raw if s["id"] == "ucell-site"][0]


def _items(page=PAGE):
    return HtmlAdapter(SourceConfig(**_cfg()))._parse_page(page, URL)


def test_only_site_requests_not_portal_duplicates():
    titles = [t.title for t in _items()]
    assert len(titles) == 2, titles
    assert titles[0].startswith("Услуги по перевозке оборудования"), titles
    assert "Таможенное" in titles[1], titles
    assert not [t for t in titles if "сканера" in t or "Jira" in t], titles


def test_deadline_and_link():
    t = _items()[0]
    assert t.deadline == "2026-10-20T16:00:00+05:00", t.deadline
    assert t.source_url == URL, t.source_url
    assert t.organization == "Ucell сайт (COSCOM)", t.organization


def test_id_is_stable_distinct_and_not_from_archive_link():
    a, b = _items(), _items()
    ids = [t.external_id for t in a]
    assert ids == [t.external_id for t in b], (ids, [t.external_id for t in b])
    assert len(set(ids)) == 2, ids
    assert not [i for i in ids if i in ("00", "7")], ids
    # тот же файл архива у нового запроса не схлопывает его со старым
    page2 = PAGE.replace("Консультационные Услуги", "Новогоднее оформление фасада здания")
    assert len(set(ids) | set(t.external_id for t in _items(page2))) == 3


def test_deadline_closes_at_its_tashkent_hour():
    # 20.10: час срока терялся, лот со сроком 16:00 жил до конца дня.
    from datetime import datetime
    from crawler.core.notifier import _is_deadline_expired
    t = _items()[0]
    assert _is_deadline_expired(t, now=datetime(2026, 10, 19, 9, 0)) is False
    assert _is_deadline_expired(t, now=datetime(2026, 10, 20, 10, 30)) is False  # 15:30 Ташкент
    assert _is_deadline_expired(t, now=datetime(2026, 10, 20, 11, 30)) is True   # 16:30 Ташкент


def test_tashkent_time_only_for_a_lone_date_with_time():
    from crawler.adapters.html import _tashkent_deadline_to_iso as f
    assert f("28.10.2026, 14:00") == "2026-10-28T14:00:00+05:00"
    assert f("1.11.2026 9:05") == "2026-11-01T09:05:00+05:00"
    for raw in ("28.10.2026", "с 01.10.2026 до 28.10.2026, 14:00", "28.10.2026, 25:00"):
        assert f(raw) == raw, raw


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
