"""Известные ошибки полного прогона не держат healthcheck красным (10.10.2026).

ИЗ ЧЕГО ВЫРОСЛО. После PR #86 HTML-адаптер перестал молчать о сбоях загрузки,
и в каждом полном прогоне появились ошибки, которые ждут внешнего события:
у Tashkent Steel истёк сертификат (09.10), Ипотека-банк не пускает IP
дата-центра (решено не чинить 11.08). `freshness.full_api` краснел бы
круглосуточно, а новая поломка другого источника тонула бы в том же алерте —
дедуп подписывает алерт именем проверки, не источником.

Здесь закреплено: известные ошибки до даты пересмотра — WARN со списком;
любая другая — FAIL с id источника; после даты запись больше не прикрывает;
ошибки без разобранного сообщения — неизвестные.

Run: .venv/bin/python3 -m pytest crawler/tests/test_known_crawl_errors.py -q
"""
import io
import os
from datetime import datetime, timedelta, timezone

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.scripts.healthcheck as H  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sources():
    with io.open(os.path.join(_ROOT, "config", "sources.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)["sources"]


def _main_profile():
    return [s["id"] for s in _sources()
            if s.get("enabled", True) and s.get("adapter") != "telegram" and not s.get("lane")]


def _run(messages, errors=None, hours_ago=0.3):
    started = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"started_at": started.isoformat(), "source_filter": _main_profile(),
            "errors_count": len(messages) if errors is None else errors,
            "error_messages": messages, "total_fetched": 9981, "total_new": 108}


TMZ = "[tashkent-steel] ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired"
IPOTEKA = "[ipoteka-bank] ConnectTimeout: "
EBIRJA = "[ebirja-announcements] Page.goto: net::ERR_NETWORK_CHANGED"


def _check(messages, today="2026-10-10", **kw):
    hc = H.HealthCheck()
    hc._check_full_api_freshness([_run(messages, **kw)], today=today)
    [res] = [r for r in hc.results if r["component"] == "freshness.full_api"]
    return res


def test_only_known_errors_are_warn_with_the_list():
    res = _check([TMZ, IPOTEKA])
    assert res["status"] == H.WARN, res["message"]
    assert "tashkent-steel (до 2026-10-17)" in res["message"]
    assert "ipoteka-bank" in res["message"]


def test_new_error_next_to_known_ones_is_fail_and_named():
    res = _check([TMZ, IPOTEKA, EBIRJA])
    assert res["status"] == H.FAIL
    assert "ebirja-announcements" in res["message"]
    assert "ended with 3 error(s)" in res["message"]


def test_known_entry_stops_covering_after_its_date():
    res = _check([TMZ], today="2026-10-18")
    assert res["status"] == H.FAIL
    assert "tashkent-steel" in res["message"]
    assert "истёк срок записи" in res["message"]


def test_last_day_of_the_entry_still_covers():
    assert _check([TMZ], today="2026-10-17")["status"] == H.WARN


def test_errors_without_messages_are_unknown():
    res = _check([], errors=2)
    assert res["status"] == H.FAIL


def test_clean_run_is_ok():
    assert _check([])["status"] == H.OK


def test_known_errors_do_not_hide_a_stale_crawl():
    res = _check([TMZ], hours_ago=9)
    assert res["status"] == H.FAIL
    assert "STALE" in res["message"]


def test_known_entries_point_at_real_enabled_sources_with_a_reason():
    enabled = set(s["id"] for s in _sources() if s.get("enabled", True))
    for sid, (until, reason) in H.KNOWN_CRAWL_ERRORS.items():
        assert sid in enabled, sid
        datetime.strptime(until, "%Y-%m-%d")
        assert len(reason) > 20


def test_ungm_is_not_excused_it_was_fixed():
    assert "ungm" not in H.KNOWN_CRAWL_ERRORS
