"""Tests for crawler.core.quality_tracker — quality measurement and regression detection."""

import json
import os
import tempfile

import pytest

from crawler.core.quality_tracker import (
    ComparisonReport,
    FieldCompleteness,
    QualitySnapshot,
    Regression,
    SourceQuality,
    _snapshot_from_dict,
    compare_snapshots,
    load_baseline,
    save_snapshot,
)
from crawler.core.models import RawTender


def _t(tid="t1", title="Test", org="ООО Test", source="etender",
       price=None, deadline=None, region="", source_url="", categories=None):
    """Helper to create a RawTender with specified fields."""
    return RawTender(
        id=tid, external_id=tid, title=title, organization=org,
        source=source, price=price, deadline=deadline, region=region,
        source_url=source_url, categories=categories or [],
    )


# ── FieldCompleteness ───────────────────────────────────────────


class TestFieldCompleteness:
    def test_empty(self):
        fc = FieldCompleteness()
        assert fc.total == 0
        assert fc.pct("org") == 0.0

    def test_all_fields_filled(self):
        fc = FieldCompleteness()
        t = _t(org="Test", price=1000, deadline="2026-05-15",
               region="Tashkent", source_url="https://x.com", categories=["print"])
        fc.add(t)
        assert fc.pct("org") == 100.0
        assert fc.pct("price") == 100.0
        assert fc.pct("deadline") == 100.0
        assert fc.pct("region") == 100.0
        assert fc.pct("source_url") == 100.0
        assert fc.pct("categories") == 100.0

    def test_partial_fields(self):
        fc = FieldCompleteness()
        fc.add(_t(org="A", price=100))    # org + price
        fc.add(_t(org="", price=None))     # nothing
        assert fc.pct("org") == 50.0
        assert fc.pct("price") == 50.0
        assert fc.pct("deadline") == 0.0

    def test_price_zero_counts_as_filled(self):
        fc = FieldCompleteness()
        fc.add(_t(price=0.0))
        assert fc.pct("price") == 100.0

    def test_empty_string_org_is_missing(self):
        fc = FieldCompleteness()
        fc.add(_t(org=""))
        assert fc.pct("org") == 0.0

    def test_to_dict(self):
        fc = FieldCompleteness()
        fc.add(_t(org="A", price=100, deadline="2026-01-01"))
        d = fc.to_dict()
        assert d["total"] == 1
        assert d["org_pct"] == 100.0
        assert d["price_pct"] == 100.0
        assert d["deadline_pct"] == 100.0
        assert d["region_pct"] == 0.0


# ── SourceQuality ───────────────────────────────────────────────


class TestSourceQuality:
    def test_score_all_filled(self):
        sq = SourceQuality("etender")
        sq.add(_t(org="A", price=100, deadline="2026-01-01",
                  region="Tashkent", source_url="https://x.com"))
        assert sq.score() == 100.0

    def test_score_nothing_filled(self):
        sq = SourceQuality("etender")
        sq.add(_t(org="", price=None, deadline=None, region="", source_url=""))
        assert sq.score() == 0.0

    def test_score_partial(self):
        sq = SourceQuality("etender")
        # Only org filled = 30% weight
        sq.add(_t(org="A", price=None, deadline=None, region="", source_url=""))
        assert sq.score() == 30.0

    def test_avg_title_len(self):
        sq = SourceQuality("etender")
        sq.add(_t(title="Hello"))      # 5
        sq.add(_t(title="World!!!!"))  # 9
        assert sq.avg_title_len == 7.0

    def test_empty_source(self):
        sq = SourceQuality("etender")
        assert sq.score() == 0.0
        assert sq.total == 0


# ── QualitySnapshot ─────────────────────────────────────────────


class TestQualitySnapshot:
    def test_from_empty_tenders(self):
        snap = QualitySnapshot.from_tenders([])
        assert snap.overall_score() == 0.0
        assert snap.overall.total == 0

    def test_from_tenders_basic(self):
        tenders = [
            _t("t1", org="A", price=100, deadline="2026-01-01", source="etender"),
            _t("t2", org="B", price=200, source="xarid"),
            _t("t3", org="", price=None, source="etender"),
        ]
        snap = QualitySnapshot.from_tenders(tenders)
        assert snap.overall.total == 3
        assert snap.overall.pct("org") == pytest.approx(66.7, abs=0.1)
        assert snap.overall.pct("price") == pytest.approx(66.7, abs=0.1)
        assert snap.overall.pct("deadline") == pytest.approx(33.3, abs=0.1)
        assert len(snap.per_source) == 2  # etender + xarid

    def test_with_source_stats(self):
        snap = QualitySnapshot.from_tenders(
            [_t()],
            source_stats={"etender": 50, "xarid": 30, "broken": 0},
        )
        assert snap.active_sources == 2
        assert snap.dead_sources == ["broken"]
        assert snap.total_fetched == 80

    def test_with_dedup_info(self):
        snap = QualitySnapshot.from_tenders(
            [_t()],
            dedup_info={"total": 100, "groups": 5, "duplicates": 10},
        )
        assert snap.dedup_total == 100
        assert snap.dedup_groups == 5
        assert snap.dedup_duplicates == 10

    def test_overall_score_averages_sources(self):
        tenders = [
            _t("t1", org="A", price=100, deadline="2026-01-01",
               region="T", source_url="https://x.com", source="good"),
            _t("t2", org="", price=None, source="bad"),
        ]
        snap = QualitySnapshot.from_tenders(tenders)
        # good = 100, bad = 0, average = 50
        assert snap.overall_score() == 50.0

    def test_serialization_roundtrip(self):
        tenders = [
            _t("t1", org="A", price=100, deadline="2026-01-01",
               region="T", source_url="https://x.com", source="etender"),
            _t("t2", org="B", source="xarid"),
        ]
        snap = QualitySnapshot.from_tenders(
            tenders,
            source_stats={"etender": 50, "xarid": 30},
            dedup_info={"total": 80, "groups": 3, "duplicates": 5},
        )
        d = snap.to_dict()
        restored = _snapshot_from_dict(d)

        assert restored.overall.total == snap.overall.total
        assert restored.active_sources == snap.active_sources
        assert restored.dedup_total == snap.dedup_total
        assert abs(restored.overall_score() - snap.overall_score()) < 2.0


# ── Regression Detection ────────────────────────────────────────


class TestRegression:
    def test_repr(self):
        r = Regression("price_pct", 80.0, 70.0, severity="warning")
        assert "80.0" in repr(r)
        assert "70.0" in repr(r)
        assert "-10.0" in repr(r)

    def test_to_dict(self):
        r = Regression("org_pct", 90.0, 75.0, severity="critical")
        d = r.to_dict()
        assert d["metric"] == "org_pct"
        assert d["delta"] == -15.0
        assert d["severity"] == "critical"


# ── compare_snapshots ───────────────────────────────────────────


class TestCompareSnapshots:
    def _make_snap(self, org_pct=80, price_pct=70, deadline_pct=60,
                   active_sources=10, dead=None):
        """Build a snapshot with preset percentages."""
        snap = QualitySnapshot()
        snap.overall.total = 100
        snap.overall.has_org = int(org_pct)
        snap.overall.has_price = int(price_pct)
        snap.overall.has_deadline = int(deadline_pct)
        snap.overall.has_region = 50
        snap.overall.has_source_url = 50
        snap.overall.has_categories = 30
        snap.active_sources = active_sources
        snap.dead_sources = dead or []
        # Need per_source for overall_score
        sq = SourceQuality("test")
        sq.total = 100
        sq.completeness = snap.overall
        snap.per_source = {"test": sq}
        return snap

    def test_no_change(self):
        baseline = self._make_snap()
        current = self._make_snap()
        report = compare_snapshots(baseline, current)
        assert not report.has_regression
        assert not report.has_critical
        assert len(report.regressions) == 0

    def test_warning_regression(self):
        baseline = self._make_snap(org_pct=80)
        current = self._make_snap(org_pct=74)  # -6pp (threshold: -5)
        report = compare_snapshots(baseline, current)
        assert report.has_regression
        assert not report.has_critical
        org_reg = [r for r in report.regressions if r.metric == "org_pct"]
        assert len(org_reg) == 1
        assert org_reg[0].severity == "warning"

    def test_critical_regression(self):
        baseline = self._make_snap(price_pct=80)
        current = self._make_snap(price_pct=60)  # -20pp (threshold: -15)
        report = compare_snapshots(baseline, current)
        assert report.has_regression
        assert report.has_critical
        price_reg = [r for r in report.regressions if r.metric == "price_pct"]
        assert len(price_reg) == 1
        assert price_reg[0].severity == "critical"

    def test_improvement_detected(self):
        baseline = self._make_snap(org_pct=60)
        current = self._make_snap(org_pct=70)  # +10pp
        report = compare_snapshots(baseline, current)
        assert not report.has_regression
        assert len(report.improvements) >= 1
        org_imp = [r for r in report.improvements if r.metric == "org_pct"]
        assert len(org_imp) == 1

    def test_source_death_detected(self):
        baseline = self._make_snap(dead=["old_dead"])
        current = self._make_snap(dead=["old_dead", "new_dead"])
        report = compare_snapshots(baseline, current)
        assert "new_dead" in report.new_dead_sources
        assert len(report.revived_sources) == 0

    def test_source_revival_detected(self):
        baseline = self._make_snap(dead=["was_dead"])
        current = self._make_snap(dead=[])
        report = compare_snapshots(baseline, current)
        assert "was_dead" in report.revived_sources
        assert len(report.new_dead_sources) == 0

    def test_active_sources_regression(self):
        baseline = self._make_snap(active_sources=10)
        current = self._make_snap(active_sources=7)  # -3 (threshold: -2)
        report = compare_snapshots(baseline, current)
        src_reg = [r for r in report.regressions if r.metric == "active_sources"]
        assert len(src_reg) == 1

    def test_multiple_regressions(self):
        baseline = self._make_snap(org_pct=80, price_pct=80)
        current = self._make_snap(org_pct=60, price_pct=60)  # both critical
        report = compare_snapshots(baseline, current)
        assert len(report.regressions) >= 2
        assert report.has_critical

    def test_small_change_is_stable(self):
        baseline = self._make_snap(org_pct=80)
        current = self._make_snap(org_pct=78)  # -2pp (below threshold)
        report = compare_snapshots(baseline, current)
        assert not report.has_regression
        assert "org_pct" in report.stable


# ── ComparisonReport ────────────────────────────────────────────


class TestComparisonReport:
    def test_summary_empty(self):
        report = ComparisonReport()
        assert "No changes" in report.summary()

    def test_summary_with_regressions(self):
        report = ComparisonReport()
        report.regressions.append(Regression("org_pct", 80, 70, "warning"))
        summary = report.summary()
        assert "REGRESSIONS" in summary
        assert "org_pct" in summary

    def test_summary_with_improvements(self):
        report = ComparisonReport()
        report.improvements.append(Regression("price_pct", 60, 80, "improvement"))
        summary = report.summary()
        assert "IMPROVEMENTS" in summary

    def test_summary_with_dead_sources(self):
        report = ComparisonReport()
        report.new_dead_sources = ["broken_api"]
        summary = report.summary()
        assert "broken_api" in summary

    def test_to_dict(self):
        report = ComparisonReport()
        report.regressions.append(Regression("org_pct", 80, 70, "warning"))
        d = report.to_dict()
        assert d["has_regression"] is True
        assert len(d["regressions"]) == 1


# ── Persistence ─────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load_baseline(self, tmp_path, monkeypatch):
        # Redirect log paths to tmp
        monkeypatch.setattr(
            "crawler.core.quality_tracker._LOG_DIR", str(tmp_path)
        )
        monkeypatch.setattr(
            "crawler.core.quality_tracker._QUALITY_LOG",
            str(tmp_path / "quality_history.jsonl"),
        )
        monkeypatch.setattr(
            "crawler.core.quality_tracker._BASELINE_FILE",
            str(tmp_path / "quality_baseline.json"),
        )

        tenders = [
            _t("t1", org="A", price=100, deadline="2026-01-01", source="etender"),
            _t("t2", org="B", source="xarid"),
        ]
        snap = QualitySnapshot.from_tenders(tenders)
        save_snapshot(snap)

        loaded = load_baseline()
        assert loaded is not None
        assert loaded.overall.total == 2
        assert abs(loaded.overall_score() - snap.overall_score()) < 2.0

    def test_history_appends(self, tmp_path, monkeypatch):
        log_path = str(tmp_path / "quality_history.jsonl")
        monkeypatch.setattr("crawler.core.quality_tracker._LOG_DIR", str(tmp_path))
        monkeypatch.setattr("crawler.core.quality_tracker._QUALITY_LOG", log_path)
        monkeypatch.setattr(
            "crawler.core.quality_tracker._BASELINE_FILE",
            str(tmp_path / "quality_baseline.json"),
        )

        snap1 = QualitySnapshot.from_tenders([_t("t1", org="A")])
        snap2 = QualitySnapshot.from_tenders([_t("t2", org="B", price=100)])

        save_snapshot(snap1)
        save_snapshot(snap2)

        with open(log_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 2

    def test_load_baseline_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "crawler.core.quality_tracker._BASELINE_FILE",
            str(tmp_path / "nonexistent.json"),
        )
        assert load_baseline() is None


# ── Integration: Full Pipeline ──────────────────────────────────


class TestQualityPipeline:
    """End-to-end: build snapshot, save, compare with next run, detect regressions."""

    def test_full_pipeline(self, tmp_path, monkeypatch):
        monkeypatch.setattr("crawler.core.quality_tracker._LOG_DIR", str(tmp_path))
        monkeypatch.setattr(
            "crawler.core.quality_tracker._QUALITY_LOG",
            str(tmp_path / "quality_history.jsonl"),
        )
        monkeypatch.setattr(
            "crawler.core.quality_tracker._BASELINE_FILE",
            str(tmp_path / "quality_baseline.json"),
        )

        # Run 1: Good quality
        tenders_v1 = [
            _t("t1", org="A", price=100, deadline="2026-01-01",
               region="T", source_url="https://x.com", source="etender"),
            _t("t2", org="B", price=200, deadline="2026-02-01",
               region="S", source_url="https://y.com", source="xarid"),
        ]
        snap1 = QualitySnapshot.from_tenders(
            tenders_v1, source_stats={"etender": 50, "xarid": 30}
        )
        save_snapshot(snap1)

        # Run 2: Degraded quality (missing org, price)
        tenders_v2 = [
            _t("t3", org="", price=None, source="etender"),
            _t("t4", org="", price=None, source="xarid"),
        ]
        snap2 = QualitySnapshot.from_tenders(
            tenders_v2, source_stats={"etender": 20, "xarid": 10, "broken": 0}
        )

        # Compare
        baseline = load_baseline()
        assert baseline is not None
        report = compare_snapshots(baseline, snap2)

        # Should detect regressions
        assert report.has_regression
        assert report.has_critical  # org and price dropped from 100% to 0%
        assert "broken" in report.new_dead_sources

        # Save v2 as new baseline
        save_snapshot(snap2)

        # Run 3: Recovery
        tenders_v3 = [
            _t("t5", org="C", price=300, deadline="2026-03-01",
               region="T", source_url="https://z.com", source="etender"),
            _t("t6", org="D", price=400, source="xarid"),
        ]
        snap3 = QualitySnapshot.from_tenders(
            tenders_v3, source_stats={"etender": 50, "xarid": 30}
        )
        baseline2 = load_baseline()
        report2 = compare_snapshots(baseline2, snap3)

        # Should detect improvements
        assert len(report2.improvements) > 0
        assert not report2.has_critical

    def test_first_run_no_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "crawler.core.quality_tracker._BASELINE_FILE",
            str(tmp_path / "nonexistent.json"),
        )

        baseline = load_baseline()
        assert baseline is None
        # First run should just save baseline, no comparison
