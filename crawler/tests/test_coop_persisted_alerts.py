"""R04: Cooperation алертит только то, что точно записано в БД (20.10.2026).

ЧТО БЫЛО (ревью 02.10). `upsert_to_supabase` проглатывал ошибку батча и
возвращал число, а `process_source` слал алерты по всем «новым» строкам
независимо от записи. 02.10 15:42 UTC «Server disconnected»: Контракты 0 / 234.
Несохранённый лот после алерта оставался «новым», и при повторе ушёл бы второй
раз — или, наоборот, алерт есть, а строки в архиве нет.

Здесь держится: батч повторяется; алерт и разведка конкурентов идут только по
записанным ключам; незаписанное остаётся на следующий прогон.

Run: .venv/bin/python3 -m pytest crawler/tests/test_coop_persisted_alerts.py -q
"""
import os
import sys
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import fetch_cooperation as fc  # noqa: E402

SRC = "Cooperation.uz Лоты"


def _rows(*ids):
    return [{"external_id": i, "source": SRC, "title": "Печать журнала %s" % i} for i in ids]


class _Client(object):
    """Отказывает первые `fail` вызовов execute(), дальше пишет."""

    def __init__(self, fail):
        self.fail = fail
        self.calls = 0

    def table(self, _name):
        return self

    def upsert(self, batch, on_conflict=None):
        return self

    def execute(self):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("Server disconnected without sending a response")
        return None


def test_failed_batch_returns_no_keys():
    assert fc.upsert_rows(_rows("a", "b"), client=_Client(fail=9), retries=1, pause=0) == set()


def test_batch_is_retried_once():
    c = _Client(fail=1)
    assert fc.upsert_rows(_rows("a", "b"), client=c, retries=1, pause=0) == {("a", SRC), ("b", SRC)}
    assert c.calls == 2


def _process(persisted):
    sent = {}
    with patch.object(fc, "get_existing_ids", return_value=set()), \
            patch.object(fc, "get_alerted_ids", return_value=set()), \
            patch.object(fc, "upsert_rows", return_value=persisted), \
            patch.object(fc, "send_alerts_unified", side_effect=lambda rows, label: sent.setdefault("alerts", [r["external_id"] for r in rows]) and len(rows)), \
            patch.object(fc, "send_competitor_alerts", side_effect=lambda rows, label: sent.setdefault("comp", [r["external_id"] for r in rows]) and 0):
        out = fc.process_source(_rows("a", "b", "c"), SRC, "Lots", gate_on_alerted=True)
    return out, sent


def test_unwritten_rows_are_not_alerted():
    out, sent = _process({("a", SRC), ("c", SRC)})
    assert sent["alerts"] == ["a", "c"] and sent["comp"] == ["a", "c"], sent
    assert out[0] == 2, out


def test_nothing_written_nothing_sent():
    out, sent = _process(set())
    assert "alerts" not in sent and "comp" not in sent, sent
    assert out[0] == 0 and out[2] == 0, out
