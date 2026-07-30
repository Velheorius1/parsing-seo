"""Tests for crawler.core.dedup — cross-source tender deduplication."""

import pytest

from crawler.core.dedup import (
    _deadlines_close,
    _extract_significant_words,
    _normalize_org,
    _parse_deadline_rough,
    _title_similarity,
    find_groups,
    group_for_alerts,
)
from crawler.core.models import RawTender


def _make_tender(
    tid="t1",
    title="Test tender",
    org="ООО Test",
    source="etender",
    deadline=None,
    price=None,
):
    """Helper to create a RawTender with minimal fields."""
    return RawTender(
        id=tid,
        external_id=tid,
        title=title,
        organization=org,
        source=source,
        deadline=deadline,
        price=price,
    )


# ── _normalize_org ──────────────────────────────────────────────


class TestNormalizeOrg:
    def test_empty_string(self):
        assert _normalize_org("") == ""

    def test_removes_legal_forms_russian(self):
        assert "рога" in _normalize_org('ООО "Рога и Копыта"')
        assert "ооо" not in _normalize_org('ООО "Рога и Копыта"')

    def test_removes_legal_forms_latin(self):
        result = _normalize_org("OOO Test Company")
        assert "ooo" not in result
        assert "test" in result

    def test_removes_quotes_and_brackets(self):
        result = _normalize_org('АО «Навоийский горно-металлургический»')
        assert "«" not in result
        assert "»" not in result

    def test_collapses_whitespace(self):
        result = _normalize_org("  ООО   Test   Company  ")
        assert "  " not in result

    def test_lowercase(self):
        assert _normalize_org("ТЕСТ") == "тест"


# ── _extract_significant_words ──────────────────────────────────


class TestExtractWords:
    def test_removes_stop_words(self):
        words = _extract_significant_words("Закупка товаров для офиса в Ташкенте")
        assert "для" not in words
        assert "в" not in words
        assert "закупка" not in words  # in stop list
        assert "ташкенте" in words

    def test_spec_tokens_are_stripped_not_kept(self):
        # Тест раньше требовал обратного — чтобы число попало в набор слов. Это
        # поведение отменено осознанно 01.07 (`c972851`, «dedup Hole 2»): тираж,
        # размер и формат вырезаются, чтобы «чек лента 80мм» и «чек лента 80г»
        # не разъезжались на два логических лота. Тест не обновили, и он месяц
        # числился «предсуществующим падением сьюта».
        # Цена решения, записанная явно: два лота одного заказчика, различающиеся
        # ТОЛЬКО тиражом, схлопнутся в один алерт.
        words = _extract_significant_words("Этикетки 5000 шт")
        assert words == {"этикетки"}, words
        assert _extract_significant_words("Чек лента 80мм") == {"чек", "лента"}
        assert _extract_significant_words("Бумага А4") == {"бумага"}

    def test_empty_string(self):
        assert _extract_significant_words("") == set()

    def test_mixed_languages(self):
        words = _extract_significant_words("Printing boxes коробки")
        assert "printing" in words
        assert "boxes" in words
        assert "коробки" in words


# ── _title_similarity ───────────────────────────────────────────


class TestTitleSimilarity:
    def test_identical(self):
        words = {"коробки", "гофро", "печать"}
        assert _title_similarity(words, words) == 1.0

    def test_no_overlap(self):
        a = {"коробки", "гофро"}
        b = {"стулья", "мебель"}
        assert _title_similarity(a, b) == 0.0

    def test_partial_overlap(self):
        a = {"коробки", "гофро", "печать", "ташкент"}
        b = {"коробки", "гофро", "картон"}
        sim = _title_similarity(a, b)
        # 2 matches / min(4, 3) = 2/3 ≈ 0.67
        assert 0.6 < sim < 0.7

    def test_empty_sets(self):
        assert _title_similarity(set(), {"a"}) == 0.0
        assert _title_similarity(set(), set()) == 0.0

    def test_subset(self):
        a = {"коробки"}
        b = {"коробки", "гофро", "печать"}
        assert _title_similarity(a, b) == 1.0  # 1/min(1,3) = 1


# ── _parse_deadline_rough ───────────────────────────────────────


class TestParseDeadline:
    def test_iso_format(self):
        assert _parse_deadline_rough("2026-05-15") == "2026-05-15"

    def test_dot_format(self):
        assert _parse_deadline_rough("15.05.2026") == "2026-05-15"

    def test_iso_with_time(self):
        assert _parse_deadline_rough("2026-05-15T18:00:00+05:00") == "2026-05-15"

    def test_none(self):
        assert _parse_deadline_rough(None) is None

    def test_garbage(self):
        assert _parse_deadline_rough("no deadline") is None

    def test_embedded_date(self):
        assert _parse_deadline_rough("Deadline: 15.05.2026 at 18:00") == "2026-05-15"


# ── _deadlines_close ────────────────────────────────────────────


class TestDeadlinesClose:
    def test_same_date(self):
        assert _deadlines_close("2026-05-15", "2026-05-15") is True

    def test_within_3_days(self):
        assert _deadlines_close("2026-05-15", "2026-05-17") is True

    def test_beyond_3_days(self):
        assert _deadlines_close("2026-05-15", "2026-05-25") is False

    def test_none_values(self):
        assert _deadlines_close(None, "2026-05-15") is True
        assert _deadlines_close(None, None) is True


# ── find_groups ─────────────────────────────────────────────────


class TestFindGroups:
    def test_empty_list(self):
        assert find_groups([]) == {}

    def test_single_tender(self):
        assert find_groups([_make_tender()]) == {}

    def test_same_source_no_group(self):
        """Tenders from the same source should NOT be grouped."""
        t1 = _make_tender("t1", "Коробки гофро 1000 шт", "ООО Test", "etender")
        t2 = _make_tender("t2", "Коробки гофро 1000 шт", "ООО Test", "etender")
        groups = find_groups([t1, t2])
        assert groups == {}

    def test_different_source_same_tender(self):
        """Same tender on different platforms should be grouped."""
        t1 = _make_tender("t1", "Коробки гофро 1000 шт", "ООО Навои", "etender", "2026-05-15")
        t2 = _make_tender("t2", "Коробки гофро 1000 шт", "ООО Навои", "xarid", "2026-05-15")
        groups = find_groups([t1, t2])
        assert "t1" in groups
        assert "t2" in groups
        assert groups["t1"] == groups["t2"]  # same group

    def test_different_org_no_group(self):
        """Different organizations should NOT be grouped."""
        t1 = _make_tender("t1", "Коробки 1000 шт", "ООО Альфа", "etender")
        t2 = _make_tender("t2", "Коробки 1000 шт", "ООО Бета", "xarid")
        groups = find_groups([t1, t2])
        assert groups == {}

    def test_similar_title_different_source(self):
        """Similar (not identical) titles from different sources should group."""
        t1 = _make_tender("t1", "Печать этикеток для молочной продукции 500000 шт", "ООО Лакто", "etender")
        t2 = _make_tender("t2", "Этикетки для молочной продукции печать", "ООО Лакто", "xarid")
        groups = find_groups([t1, t2])
        assert "t1" in groups
        assert "t2" in groups

    def test_deadline_too_far_no_group(self):
        """Same tender but deadlines > 3 days apart should NOT group."""
        t1 = _make_tender("t1", "Коробки 1000 шт", "ООО Test", "etender", "2026-05-15")
        t2 = _make_tender("t2", "Коробки 1000 шт", "ООО Test", "xarid", "2026-06-15")
        groups = find_groups([t1, t2])
        assert groups == {}

    def test_empty_org_still_groups_by_title(self):
        """Tenders with empty org should still group if titles match."""
        t1 = _make_tender("t1", "Коробки гофро 5000 шт", "", "etender")
        t2 = _make_tender("t2", "Коробки гофро 5000 шт", "", "xarid")
        groups = find_groups([t1, t2])
        # Empty org means org check is skipped (both empty)
        assert "t1" in groups
        assert "t2" in groups

    def test_three_sources_one_group(self):
        """Same tender on 3 platforms = one group."""
        tenders = [
            _make_tender("t1", "Этикетки для продуктов", "ООО Арт", "etender"),
            _make_tender("t2", "Этикетки для продуктов", "ООО Арт", "xarid"),
            _make_tender("t3", "Этикетки для продуктов", "ООО Арт", "cooperation"),
        ]
        groups = find_groups(tenders)
        assert len(set(groups.values())) == 1  # one group
        assert len(groups) == 3  # all three in it


# ── group_for_alerts ────────────────────────────────────────────


class TestGroupForAlerts:
    def test_empty(self):
        result, sources = group_for_alerts([], [])
        assert result == []
        assert sources == {}

    def test_unique_tenders_pass_through(self):
        tenders = [
            _make_tender("t1", "Коробки", "ООО А", "etender"),
            _make_tender("t2", "Этикетки", "ООО Б", "etender"),
        ]
        result, sources = group_for_alerts(tenders, tenders)
        assert len(result) == 2

    def test_duplicates_merged(self):
        t1 = _make_tender("t1", "Коробки гофро 1000", "ООО Test", "etender")
        t2 = _make_tender("t2", "Коробки гофро 1000", "ООО Test", "xarid")
        result, sources = group_for_alerts([t1, t2], [t1, t2])
        assert len(result) == 1  # one representative
        # The representative should have both sources
        rep = result[0]
        assert rep.id in sources
        assert len(sources[rep.id]) == 2
