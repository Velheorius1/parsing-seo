"""Tests for crawler.core.results_tracker — niche-results dedup state.

Covers:
- Loading state when key is missing → empty set
- Loading malformed state → empty set (no crash)
- FIFO cap enforcement at _NICHE_ALERTED_CAP
- Merge keeps insertion order, drops duplicates
"""

import pytest

from crawler.core import results_tracker as rt


class _FakeStore:
    """In-memory stand-in for session_store with the same get/set API."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get_setting(self, key):
        return self._data.get(key)

    def set_setting(self, key, value):
        self._data[key] = value
        return True


def _patch_store(monkeypatch, fake):
    monkeypatch.setattr(
        "crawler.auth.session_store.session_store",
        fake,
    )


# ── _load_alerted_ids ────────────────────────────────────────────


def test_load_empty_when_missing(monkeypatch):
    _patch_store(monkeypatch, _FakeStore())
    assert rt._load_alerted_ids() == []


def test_load_returns_ordered_list(monkeypatch):
    fake = _FakeStore({
        rt._NICHE_ALERTED_STATE_KEY: {"alerted_ids": ["result-1", "result-2", "result-3"]},
    })
    _patch_store(monkeypatch, fake)
    # Order preserved (oldest first)
    assert rt._load_alerted_ids() == ["result-1", "result-2", "result-3"]


def test_load_malformed_state_returns_empty(monkeypatch):
    fake = _FakeStore({rt._NICHE_ALERTED_STATE_KEY: "not a dict"})
    _patch_store(monkeypatch, fake)
    assert rt._load_alerted_ids() == []


def test_load_missing_alerted_ids_key(monkeypatch):
    fake = _FakeStore({rt._NICHE_ALERTED_STATE_KEY: {"other_key": "value"}})
    _patch_store(monkeypatch, fake)
    assert rt._load_alerted_ids() == []


def test_load_alerted_ids_not_list(monkeypatch):
    fake = _FakeStore({rt._NICHE_ALERTED_STATE_KEY: {"alerted_ids": "not a list"}})
    _patch_store(monkeypatch, fake)
    assert rt._load_alerted_ids() == []


def test_load_skips_falsy_entries(monkeypatch):
    fake = _FakeStore({
        rt._NICHE_ALERTED_STATE_KEY: {"alerted_ids": ["result-1", "", None, "result-2"]},
    })
    _patch_store(monkeypatch, fake)
    assert rt._load_alerted_ids() == ["result-1", "result-2"]


# ── _record_alerted_ids ──────────────────────────────────────────


def test_record_writes_merged_state(monkeypatch):
    fake = _FakeStore()
    _patch_store(monkeypatch, fake)

    ok = rt._record_alerted_ids(["result-1"], ["result-2", "result-3"])
    assert ok is True
    saved = fake._data[rt._NICHE_ALERTED_STATE_KEY]
    assert saved["alerted_ids"] == ["result-1", "result-2", "result-3"]
    assert "updated_at" in saved


def test_record_drops_duplicates_preserving_order(monkeypatch):
    fake = _FakeStore()
    _patch_store(monkeypatch, fake)

    rt._record_alerted_ids(["result-1", "result-2"], ["result-2", "result-3"])
    saved_ids = fake._data[rt._NICHE_ALERTED_STATE_KEY]["alerted_ids"]
    # No duplicates AND order preserved — old first, then new
    assert saved_ids == ["result-1", "result-2", "result-3"]


def test_record_fifo_cap_enforced(monkeypatch):
    fake = _FakeStore()
    _patch_store(monkeypatch, fake)
    monkeypatch.setattr(rt, "_NICHE_ALERTED_CAP", 5)

    previous = ["old-%d" % i for i in range(4)]  # old-0..old-3 (oldest first)
    new = ["new-%d" % i for i in range(4)]

    rt._record_alerted_ids(previous, new)
    saved_ids = fake._data[rt._NICHE_ALERTED_STATE_KEY]["alerted_ids"]
    # 8 inputs, cap=5 → keep tail (most-recent 5)
    assert saved_ids == ["old-3", "new-0", "new-1", "new-2", "new-3"]


def test_record_skips_empty_strings(monkeypatch):
    fake = _FakeStore()
    _patch_store(monkeypatch, fake)

    rt._record_alerted_ids([], ["result-1", "", None, "result-2"])
    saved_ids = fake._data[rt._NICHE_ALERTED_STATE_KEY]["alerted_ids"]
    assert saved_ids == ["result-1", "result-2"]
