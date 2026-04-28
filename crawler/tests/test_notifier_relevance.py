"""Tests for crawler.core.notifier — structured AI relevance (migration 017).

Covers:
- JSON parsing tolerant to fences and prose
- Out-of-range / wrong-type score handling
- Unknown category coercion
- Threshold semantics
- Fallback policy on AI errors (None score, is_relevant=True)
"""

import pytest

from crawler.core.notifier import (
    RelevanceResult,
    _allow,
    _extract_json_object,
    _parse_relevance_payload,
    _strip_think_tags,
)


# ── _strip_think_tags ─────────────────────────────────────────────


def test_strip_think_tags_no_tags():
    assert _strip_think_tags("YES") == "YES"


def test_strip_think_tags_simple():
    assert _strip_think_tags("<think>thinking</think>YES") == "YES"


def test_strip_think_tags_multiline():
    text = "<think>line1\nline2\nline3</think>\n{\"score\": 95}"
    assert _strip_think_tags(text) == '{"score": 95}'


# ── _extract_json_object ──────────────────────────────────────────


def test_extract_json_plain():
    obj = _extract_json_object('{"score": 90, "category": "client", "reason": "ok"}')
    assert obj == {"score": 90, "category": "client", "reason": "ok"}


def test_extract_json_with_prose():
    text = 'Here is my answer: {"score": 80, "category": "client", "reason": "boxes"} done.'
    obj = _extract_json_object(text)
    assert obj is not None
    assert obj["score"] == 80


def test_extract_json_fenced():
    text = '```json\n{"score": 50, "category": "irrelevant", "reason": "no"}\n```'
    obj = _extract_json_object(text)
    assert obj == {"score": 50, "category": "irrelevant", "reason": "no"}


def test_extract_json_fenced_no_lang():
    text = '```\n{"score": 75}\n```'
    obj = _extract_json_object(text)
    assert obj == {"score": 75}


def test_extract_json_none_when_missing():
    assert _extract_json_object("YES") is None
    assert _extract_json_object("") is None


def test_extract_json_none_on_invalid():
    assert _extract_json_object("{not json}") is None


# ── _parse_relevance_payload ──────────────────────────────────────


def test_parse_payload_valid_client(monkeypatch):
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "ai_score_threshold", 70)

    r = _parse_relevance_payload({"score": 90, "category": "client", "reason": "коробки"})
    assert r is not None
    assert r.is_relevant is True
    assert r.score == 90
    assert r.category == "client"
    assert r.reason == "коробки"


def test_parse_payload_below_threshold(monkeypatch):
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "ai_score_threshold", 70)

    r = _parse_relevance_payload({"score": 30, "category": "irrelevant", "reason": "стройка"})
    assert r.is_relevant is False
    assert r.score == 30
    assert r.category == "irrelevant"


def test_parse_payload_at_threshold(monkeypatch):
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "ai_score_threshold", 70)

    r = _parse_relevance_payload({"score": 70, "category": "client", "reason": ""})
    assert r.is_relevant is True


def test_parse_payload_score_clamp_high(monkeypatch):
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "ai_score_threshold", 70)

    r = _parse_relevance_payload({"score": 150, "category": "client", "reason": "x"})
    assert r.score == 100
    assert r.is_relevant is True


def test_parse_payload_score_clamp_low(monkeypatch):
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "ai_score_threshold", 70)

    r = _parse_relevance_payload({"score": -10, "category": "irrelevant", "reason": "x"})
    assert r.score == 0
    assert r.is_relevant is False


def test_parse_payload_score_string_numeric():
    r = _parse_relevance_payload({"score": "85", "category": "client", "reason": "x"})
    assert r is not None
    assert r.score == 85


def test_parse_payload_score_invalid_returns_none():
    assert _parse_relevance_payload({"score": "high", "category": "client"}) is None
    assert _parse_relevance_payload({"category": "client"}) is None


def test_parse_payload_unknown_category_high_score(monkeypatch):
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "ai_score_threshold", 70)

    # AI returned "tender" — coerce by score
    r = _parse_relevance_payload({"score": 85, "category": "tender", "reason": "x"})
    assert r.category == "client"


def test_parse_payload_unknown_category_low_score(monkeypatch):
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "ai_score_threshold", 70)

    r = _parse_relevance_payload({"score": 30, "category": "spam", "reason": "x"})
    assert r.category == "irrelevant"


def test_parse_payload_reason_truncated():
    long_reason = "x" * 500
    r = _parse_relevance_payload({"score": 80, "category": "client", "reason": long_reason})
    assert len(r.reason) == 200


def test_parse_payload_reason_missing_ok():
    r = _parse_relevance_payload({"score": 80, "category": "client"})
    assert r.reason == ""


# ── RelevanceResult bool semantics ────────────────────────────────


def test_relevance_result_truthy_when_relevant():
    r = RelevanceResult(is_relevant=True, score=90, category="client", reason="x")
    assert bool(r) is True
    assert r  # for `if result:` callers


def test_relevance_result_falsy_when_not_relevant():
    r = RelevanceResult(is_relevant=False, score=20, category="irrelevant", reason="x")
    assert bool(r) is False


# ── Fallback ──────────────────────────────────────────────────────


def test_allow_fallback_returns_relevant_with_no_score():
    r = _allow("no_key")
    assert r.is_relevant is True
    assert r.score is None
    assert r.category is None
    assert r.reason is None
