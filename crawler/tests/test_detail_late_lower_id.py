"""R02: лот с меньшим номером, появившийся позже соседей, получает спецификацию.

ЧТО БЫЛО (ревью 02.10.2026). Detail-страницы качались только для id больше
курсора max_seen. В активную выдачу etender лоты попадают не в порядке номеров:
512262 «Tafakkur jurnalini 3-soni» (печать журнала) появился 22.09 ниже курсора,
остался без спецификации — в заголовке только ОКЭД-категория — и без алерта.

Здесь держится: кандидат — любой id выше пола, ещё не обработанный; старый state
без пола не качает архив заново; сбой не помечает лот обработанным; срез
«newest» не тянет хвост архива; вытесненные из окна id уходят под пол.

Run: .venv/bin/python3 -m pytest crawler/tests/test_detail_late_lower_id.py -q
"""
import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters import api as A  # noqa: E402
from crawler.auth.session_store import session_store  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402


def _config(bootstrap="oldest", budget=150):
    return SourceConfig(
        id="late-test", name="Late Test", adapter="api", url="https://example.test",
        id_prefix="lt", field_map={"title": "title", "external_id": "id"},
        keywords_fields=["title", "_detail_text"],
        detail_fetch={"url_template": "https://example.test/{id}", "state_key": "late-state",
                      "text_fields": ["details.*.name"], "bootstrap": bootstrap,
                      "max_per_run": budget, "retries": 0},
    )


def _run(ids, state, fail=(), bootstrap="oldest", budget=150):
    """Один прогон: (какие id качались, записанный state или None)."""
    fetched = []

    class _Resp(object):
        def __init__(self, iid):
            self.text = json.dumps({"details": [{"name": "печать журнала %s" % iid}]})

        def raise_for_status(self):
            return None

    class _Client(object):
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url):
            iid = int(url.rsplit("/", 1)[1])
            fetched.append(iid)
            if iid in fail:
                raise RuntimeError("timeout")
            return _Resp(iid)

    adapter = A.ApiAdapter(_config(bootstrap, budget))
    adapter.rate_limit = AsyncMock()
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"PARSING_DETAIL_CACHE_PATH": os.path.join(tmp, "c.json")}), \
                patch.object(session_store, "get_setting", return_value=state), \
                patch.object(session_store, "set_setting") as put, \
                patch("crawler.adapters.api.httpx.AsyncClient", _Client):
            asyncio.run(adapter._enrich_with_details([{"id": i, "title": "ОКЭД"} for i in ids]))
    return fetched, (put.call_args[0][1] if put.called else None)


def test_late_lower_id_gets_its_detail():
    fetched, state = _run([60, 70, 100, 101], {"max_seen_id": 100, "seen_floor": 50, "seen_ids": [60, 100]})
    assert fetched == [70, 101], fetched
    assert state["seen_ids"] == [60, 70, 100, 101] and state["max_seen_id"] == 101, state
    again, _ = _run([60, 70, 100, 101], state)
    assert again == [], again


def test_old_state_without_floor_does_not_refetch_the_archive():
    fetched, state = _run([90, 95, 100, 101], {"max_seen_id": 100})
    assert fetched == [101], fetched
    assert state["seen_floor"] == 100 and state["seen_ids"] == [101], state
    late, _ = _run([90, 100, 101, 102], state)  # 102 — новый
    assert late == [102], late


def test_failed_detail_is_retried_next_run_and_does_not_block_newer_lots():
    # 20.10: detail 520753 отдавал пустое тело, и break оставлял без спецификации
    # все более новые лоты etender — с 12:00 до фикса.
    fetched, state = _run([70, 101, 102], {"max_seen_id": 100, "seen_floor": 50, "seen_ids": [100]}, fail={70})
    assert fetched == [70, 101, 102], fetched
    assert state["seen_ids"] == [100, 101, 102], state
    retry, state = _run([70, 101, 102], state)
    assert retry == [70] and 70 in state["seen_ids"], (retry, state)


def test_newest_bootstrap_does_not_pull_the_archive_tail():
    fetched, state = _run(list(range(1, 11)), None, bootstrap="newest", budget=2)
    assert fetched == [9, 10], fetched
    assert state["seen_floor"] == 8, state
    again, _ = _run(list(range(1, 11)), state, bootstrap="newest", budget=2)
    assert again == [], again


def test_ids_pushed_out_of_the_window_go_under_the_floor():
    with patch.object(A, "_SEEN_KEEP", 3):
        fetched, state = _run([1, 2, 3, 5], {"max_seen_id": 3, "seen_floor": 0, "seen_ids": [1, 2, 3]})
        assert fetched == [5], fetched
        assert state["seen_floor"] == 1 and state["seen_ids"] == [2, 3, 5], state
        late, _ = _run([2, 3, 4, 5], state)  # 4 выше пола и не обработан
    assert late == [4], late


def test_failing_detail_goes_last_and_is_given_up_after_a_cap():
    # Ревью 20.10: лот, который никогда не отдаёт detail, повторялся каждый
    # прогон без предела и стоял первым в бюджете.
    state = {"max_seen_id": 100, "seen_floor": 50, "seen_ids": [], "fail_counts": {"60": 1}}
    fetched, state = _run([60, 70], state, fail={60}, budget=1)
    assert fetched == [70], fetched  # падавший — после свежего, бюджет 1
    assert state["fail_counts"] == {"60": 1}, state
    for _ in range(A._MAX_DETAIL_FAILS - 1):
        fetched, state = _run([60, 70], state, fail={60})
    assert 60 in state["seen_ids"] and state["fail_counts"] == {}, state
    after, _ = _run([60, 70], state, fail={60})
    assert after == [], after
