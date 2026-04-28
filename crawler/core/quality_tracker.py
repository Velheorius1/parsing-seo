"""Quality Tracker — measures and compares crawl data quality across runs.

Ensures changes don't degrade results. Tracks:
- Field completeness (org, price, deadline, region, source_url)
- Source coverage (active sources vs expected)
- Dedup effectiveness (duplicates found vs total)
- Data freshness (tenders with valid dates)
- Per-source quality scores

Usage:
    snapshot = QualitySnapshot.from_tenders(tenders, stats)
    report = compare_snapshots(previous, current)
    if report.has_regression:
        alert(report.regressions)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)
_QUALITY_LOG = os.path.join(_LOG_DIR, "quality_history.jsonl")
_BASELINE_FILE = os.path.join(_LOG_DIR, "quality_baseline.json")


class FieldCompleteness:
    """Percentage of tenders with each field filled."""

    __slots__ = (
        "total",
        "has_org",
        "has_price",
        "has_deadline",
        "has_region",
        "has_source_url",
        "has_categories",
    )

    def __init__(self):
        # type: () -> None
        self.total = 0
        self.has_org = 0
        self.has_price = 0
        self.has_deadline = 0
        self.has_region = 0
        self.has_source_url = 0
        self.has_categories = 0

    def add(self, tender):
        # type: (Any) -> None
        self.total += 1
        if tender.organization:
            self.has_org += 1
        if tender.price is not None:
            self.has_price += 1
        if tender.deadline:
            self.has_deadline += 1
        if tender.region:
            self.has_region += 1
        if tender.source_url:
            self.has_source_url += 1
        if tender.categories:
            self.has_categories += 1

    def pct(self, field):
        # type: (str) -> float
        if self.total == 0:
            return 0.0
        value = getattr(self, "has_%s" % field, 0)
        return round(value / self.total * 100, 1)

    def to_dict(self):
        # type: () -> Dict[str, float]
        return {
            "total": self.total,
            "org_pct": self.pct("org"),
            "price_pct": self.pct("price"),
            "deadline_pct": self.pct("deadline"),
            "region_pct": self.pct("region"),
            "source_url_pct": self.pct("source_url"),
            "categories_pct": self.pct("categories"),
        }


class SourceQuality:
    """Per-source quality metrics."""

    __slots__ = ("source_id", "total", "completeness", "avg_title_len")

    def __init__(self, source_id):
        # type: (str) -> None
        self.source_id = source_id
        self.total = 0
        self.completeness = FieldCompleteness()
        self.avg_title_len = 0.0

    def add(self, tender):
        # type: (Any) -> None
        self.total += 1
        self.completeness.add(tender)
        # Running average of title length
        self.avg_title_len = (
            (self.avg_title_len * (self.total - 1) + len(tender.title)) / self.total
        )

    def score(self):
        # type: () -> float
        """Quality score 0-100. Weighted average of field completeness."""
        c = self.completeness
        if c.total == 0:
            return 0.0
        # Weights: org=30, price=30, deadline=20, region=10, source_url=10
        return round(
            c.pct("org") * 0.30
            + c.pct("price") * 0.30
            + c.pct("deadline") * 0.20
            + c.pct("region") * 0.10
            + c.pct("source_url") * 0.10,
            1,
        )

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            "source_id": self.source_id,
            "total": self.total,
            "score": self.score(),
            "avg_title_len": round(self.avg_title_len, 1),
            "completeness": self.completeness.to_dict(),
        }


class QualitySnapshot:
    """Point-in-time quality measurement of a crawl run."""

    def __init__(self):
        # type: () -> None
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.overall = FieldCompleteness()
        self.per_source = {}  # type: Dict[str, SourceQuality]
        self.source_stats = {}  # type: Dict[str, int]
        self.dedup_total = 0
        self.dedup_groups = 0
        self.dedup_duplicates = 0
        self.total_fetched = 0
        self.total_new = 0
        self.enriched = 0
        self.alerts_sent = 0
        self.errors_count = 0
        self.active_sources = 0
        self.dead_sources = []  # type: List[str]

    @classmethod
    def from_tenders(cls, tenders, source_stats=None, dedup_info=None):
        # type: (List[Any], Optional[Dict[str, int]], Optional[Dict]) -> QualitySnapshot
        """Build snapshot from list of RawTenders + optional stats."""
        snap = cls()

        for t in tenders:
            snap.overall.add(t)
            sid = t.source
            if sid not in snap.per_source:
                snap.per_source[sid] = SourceQuality(sid)
            snap.per_source[sid].add(t)

        if source_stats:
            snap.source_stats = dict(source_stats)
            snap.active_sources = sum(1 for c in source_stats.values() if c > 0)
            snap.dead_sources = [s for s, c in source_stats.items() if c == 0]
            snap.total_fetched = sum(source_stats.values())

        if dedup_info:
            snap.dedup_total = dedup_info.get("total", 0)
            snap.dedup_groups = dedup_info.get("groups", 0)
            snap.dedup_duplicates = dedup_info.get("duplicates", 0)

        return snap

    def overall_score(self):
        # type: () -> float
        """Aggregate quality score 0-100."""
        if not self.per_source:
            return 0.0
        scores = [sq.score() for sq in self.per_source.values() if sq.total > 0]
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 1)

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            "timestamp": self.timestamp,
            "overall_score": self.overall_score(),
            "overall_completeness": self.overall.to_dict(),
            "per_source": {
                sid: sq.to_dict() for sid, sq in self.per_source.items()
            },
            "source_stats": self.source_stats,
            "active_sources": self.active_sources,
            "dead_sources": self.dead_sources,
            "dedup": {
                "total": self.dedup_total,
                "groups": self.dedup_groups,
                "duplicates": self.dedup_duplicates,
            },
            "total_fetched": self.total_fetched,
            "total_new": self.total_new,
            "enriched": self.enriched,
            "alerts_sent": self.alerts_sent,
            "errors_count": self.errors_count,
        }


class Regression:
    """A detected quality regression."""

    __slots__ = ("metric", "baseline_value", "current_value", "delta", "severity")

    def __init__(self, metric, baseline, current, severity="warning"):
        # type: (str, float, float, str) -> None
        self.metric = metric
        self.baseline_value = baseline
        self.current_value = current
        self.delta = current - baseline
        self.severity = severity  # "warning" or "critical"

    def __repr__(self):
        # type: () -> str
        return "%s: %.1f -> %.1f (%+.1f) [%s]" % (
            self.metric, self.baseline_value, self.current_value,
            self.delta, self.severity,
        )

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            "metric": self.metric,
            "baseline": self.baseline_value,
            "current": self.current_value,
            "delta": self.delta,
            "severity": self.severity,
        }


class ComparisonReport:
    """Result of comparing two quality snapshots."""

    def __init__(self):
        # type: () -> None
        self.regressions = []  # type: List[Regression]
        self.improvements = []  # type: List[Regression]
        self.stable = []  # type: List[str]
        self.new_dead_sources = []  # type: List[str]
        self.revived_sources = []  # type: List[str]

    @property
    def has_regression(self):
        # type: () -> bool
        return len(self.regressions) > 0

    @property
    def has_critical(self):
        # type: () -> bool
        return any(r.severity == "critical" for r in self.regressions)

    def summary(self):
        # type: () -> str
        lines = []
        if self.regressions:
            lines.append("REGRESSIONS (%d):" % len(self.regressions))
            for r in self.regressions:
                lines.append("  - %s" % r)
        if self.improvements:
            lines.append("IMPROVEMENTS (%d):" % len(self.improvements))
            for r in self.improvements:
                lines.append("  + %s" % r)
        if self.new_dead_sources:
            lines.append("NEW DEAD SOURCES: %s" % ", ".join(self.new_dead_sources))
        if self.revived_sources:
            lines.append("REVIVED SOURCES: %s" % ", ".join(self.revived_sources))
        if not lines:
            lines.append("No changes detected.")
        return "\n".join(lines)

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            "has_regression": self.has_regression,
            "has_critical": self.has_critical,
            "regressions": [r.to_dict() for r in self.regressions],
            "improvements": [r.to_dict() for r in self.improvements],
            "stable_metrics": self.stable,
            "new_dead_sources": self.new_dead_sources,
            "revived_sources": self.revived_sources,
        }


# ── Thresholds for regression detection ─────────────────────────

# Minimum drop (in percentage points) to trigger a regression
_THRESHOLDS = {
    "org_pct": {"warning": -5.0, "critical": -15.0},
    "price_pct": {"warning": -5.0, "critical": -15.0},
    "deadline_pct": {"warning": -5.0, "critical": -15.0},
    "region_pct": {"warning": -10.0, "critical": -25.0},
    "source_url_pct": {"warning": -10.0, "critical": -25.0},
    "overall_score": {"warning": -3.0, "critical": -10.0},
    "active_sources": {"warning": -2, "critical": -5},
}


def compare_snapshots(baseline, current):
    # type: (QualitySnapshot, QualitySnapshot) -> ComparisonReport
    """Compare two snapshots and detect regressions/improvements."""
    report = ComparisonReport()

    # Compare overall completeness
    b_comp = baseline.overall.to_dict()
    c_comp = current.overall.to_dict()

    for field in ("org_pct", "price_pct", "deadline_pct", "region_pct", "source_url_pct"):
        b_val = b_comp.get(field, 0)
        c_val = c_comp.get(field, 0)
        delta = c_val - b_val

        thresholds = _THRESHOLDS.get(field, {"warning": -5.0, "critical": -15.0})

        if delta <= thresholds["critical"]:
            report.regressions.append(
                Regression(field, b_val, c_val, severity="critical")
            )
        elif delta <= thresholds["warning"]:
            report.regressions.append(
                Regression(field, b_val, c_val, severity="warning")
            )
        elif delta >= abs(thresholds["warning"]):
            report.improvements.append(
                Regression(field, b_val, c_val, severity="improvement")
            )
        else:
            report.stable.append(field)

    # Compare overall score
    b_score = baseline.overall_score()
    c_score = current.overall_score()
    score_delta = c_score - b_score
    if score_delta <= _THRESHOLDS["overall_score"]["critical"]:
        report.regressions.append(
            Regression("overall_score", b_score, c_score, severity="critical")
        )
    elif score_delta <= _THRESHOLDS["overall_score"]["warning"]:
        report.regressions.append(
            Regression("overall_score", b_score, c_score, severity="warning")
        )

    # Compare active sources
    b_active = baseline.active_sources
    c_active = current.active_sources
    if c_active < b_active:
        delta = c_active - b_active
        sev = "critical" if delta <= _THRESHOLDS["active_sources"]["critical"] else "warning"
        report.regressions.append(
            Regression("active_sources", float(b_active), float(c_active), severity=sev)
        )

    # Source lifecycle: new dead / revived
    b_dead = set(baseline.dead_sources)
    c_dead = set(current.dead_sources)
    report.new_dead_sources = sorted(c_dead - b_dead)
    report.revived_sources = sorted(b_dead - c_dead)

    return report


# ── Persistence ──────────────────────────────────────────────────


def save_snapshot(snapshot):
    # type: (QualitySnapshot) -> None
    """Append snapshot to JSONL history and update baseline."""
    os.makedirs(_LOG_DIR, exist_ok=True)

    data = snapshot.to_dict()

    # Append to history
    try:
        with open(_QUALITY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed to write quality log: %s", str(exc)[:100])

    # Update baseline (latest snapshot is the new baseline)
    try:
        with open(_BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Failed to write quality baseline: %s", str(exc)[:100])


def flush_snapshot_to_supabase(snapshot):
    # type: (QualitySnapshot) -> int
    """Insert per-source quality metrics into source_quality_metrics table.

    Returns count of rows inserted. Returns 0 and logs warning if the table
    doesn't exist (graceful fallback for environments where migration 016
    hasn't been applied yet).
    """
    try:
        from crawler.config.settings import settings
        if not settings.supabase_url or not settings.supabase_service_role_key:
            logger.warning("[quality] No supabase credentials, skipping flush")
            return 0
        from supabase import create_client
        client = create_client(settings.supabase_url, settings.supabase_service_role_key)

        rows = []
        ts = snapshot.timestamp
        for sid, sq in snapshot.per_source.items():
            sample = sq.total
            if sample <= 0:
                continue
            comp = sq.completeness
            rows.append({
                "source": sid, "metric_type": "items_kept_after_filter",
                "metric_value": float(sample), "sample_size": sample, "computed_at": ts,
            })
            rows.append({
                "source": sid, "metric_type": "quality_score",
                "metric_value": float(sq.score()), "sample_size": sample, "computed_at": ts,
            })
            rows.append({
                "source": sid, "metric_type": "org_pct",
                "metric_value": float(comp.org_pct() if hasattr(comp, "org_pct") else 0),
                "sample_size": sample, "computed_at": ts,
            })
            rows.append({
                "source": sid, "metric_type": "price_pct",
                "metric_value": float(comp.price_pct() if hasattr(comp, "price_pct") else 0),
                "sample_size": sample, "computed_at": ts,
            })
            rows.append({
                "source": sid, "metric_type": "deadline_pct",
                "metric_value": float(comp.deadline_pct() if hasattr(comp, "deadline_pct") else 0),
                "sample_size": sample, "computed_at": ts,
            })

        # Also write items_fetched per source (including 0 for dead sources)
        for sid, count in (snapshot.source_stats or {}).items():
            rows.append({
                "source": sid, "metric_type": "items_fetched",
                "metric_value": float(count), "sample_size": count, "computed_at": ts,
            })

        if not rows:
            return 0

        # Batch insert (chunks of 500 to stay well under PostgREST limits)
        inserted = 0
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            try:
                client.table("source_quality_metrics").insert(chunk).execute()
                inserted += len(chunk)
            except Exception as e:
                msg = str(e).lower()
                if "does not exist" in msg or "relation" in msg or "schema" in msg:
                    logger.warning(
                        "[quality] source_quality_metrics table missing — "
                        "apply migration 016 via Supabase Studio. Skipping flush."
                    )
                    return 0
                raise
        logger.info("[quality] Flushed %d metrics to Supabase", inserted)
        return inserted
    except Exception as exc:
        logger.warning("[quality] flush_to_supabase failed: %s", str(exc)[:200])
        return 0


def load_baseline():
    # type: () -> Optional[QualitySnapshot]
    """Load the latest baseline snapshot."""
    if not os.path.exists(_BASELINE_FILE):
        return None

    try:
        with open(_BASELINE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _snapshot_from_dict(data)
    except Exception as exc:
        logger.warning("Failed to load quality baseline: %s", str(exc)[:100])
        return None


def load_history(last_n=20):
    # type: (int) -> List[Dict[str, Any]]
    """Load last N quality snapshots from history."""
    if not os.path.exists(_QUALITY_LOG):
        return []

    entries = []
    with open(_QUALITY_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries[-last_n:]


def _snapshot_from_dict(data):
    # type: (Dict[str, Any]) -> QualitySnapshot
    """Reconstruct QualitySnapshot from serialized dict."""
    snap = QualitySnapshot()
    snap.timestamp = data.get("timestamp", "")
    snap.active_sources = data.get("active_sources", 0)
    snap.dead_sources = data.get("dead_sources", [])
    snap.total_fetched = data.get("total_fetched", 0)
    snap.total_new = data.get("total_new", 0)
    snap.enriched = data.get("enriched", 0)
    snap.alerts_sent = data.get("alerts_sent", 0)
    snap.errors_count = data.get("errors_count", 0)
    snap.source_stats = data.get("source_stats", {})

    # Reconstruct overall completeness
    oc = data.get("overall_completeness", {})
    snap.overall.total = oc.get("total", 0)
    if snap.overall.total > 0:
        snap.overall.has_org = int(oc.get("org_pct", 0) * snap.overall.total / 100)
        snap.overall.has_price = int(oc.get("price_pct", 0) * snap.overall.total / 100)
        snap.overall.has_deadline = int(oc.get("deadline_pct", 0) * snap.overall.total / 100)
        snap.overall.has_region = int(oc.get("region_pct", 0) * snap.overall.total / 100)
        snap.overall.has_source_url = int(oc.get("source_url_pct", 0) * snap.overall.total / 100)
        snap.overall.has_categories = int(oc.get("categories_pct", 0) * snap.overall.total / 100)

    # Reconstruct per-source from dict
    for sid, sq_data in data.get("per_source", {}).items():
        sq = SourceQuality(sid)
        sq.total = sq_data.get("total", 0)
        sq.avg_title_len = sq_data.get("avg_title_len", 0)
        comp = sq_data.get("completeness", {})
        sq.completeness.total = comp.get("total", 0)
        if sq.completeness.total > 0:
            sq.completeness.has_org = int(comp.get("org_pct", 0) * sq.completeness.total / 100)
            sq.completeness.has_price = int(comp.get("price_pct", 0) * sq.completeness.total / 100)
            sq.completeness.has_deadline = int(comp.get("deadline_pct", 0) * sq.completeness.total / 100)
            sq.completeness.has_region = int(comp.get("region_pct", 0) * sq.completeness.total / 100)
            sq.completeness.has_source_url = int(comp.get("source_url_pct", 0) * sq.completeness.total / 100)
            sq.completeness.has_categories = int(comp.get("categories_pct", 0) * sq.completeness.total / 100)
        snap.per_source[sid] = sq

    dedup = data.get("dedup", {})
    snap.dedup_total = dedup.get("total", 0)
    snap.dedup_groups = dedup.get("groups", 0)
    snap.dedup_duplicates = dedup.get("duplicates", 0)

    return snap


# ── CLI ──────────────────────────────────────────────────────────


def print_quality_report(last_n=5):
    # type: (int) -> None
    """Print quality trend from history."""
    history = load_history(last_n)
    if not history:
        print("No quality history yet. Run the crawler first.")
        return

    print("\n=== Quality Trend (last %d runs) ===" % len(history))
    print("-" * 95)
    print("%-20s %6s %6s %6s %6s %6s %6s %6s" % (
        "Timestamp", "Score", "Org%", "Price%", "Dead%", "Srcs", "Fetch", "Errs",
    ))
    print("-" * 95)

    for entry in history:
        ts = entry.get("timestamp", "?")[:19]
        score = entry.get("overall_score", 0)
        comp = entry.get("overall_completeness", {})
        print("%-20s %6.1f %6.1f %6.1f %6.1f %6d %6d %6d" % (
            ts,
            score,
            comp.get("org_pct", 0),
            comp.get("price_pct", 0),
            comp.get("deadline_pct", 0),
            entry.get("active_sources", 0),
            entry.get("total_fetched", 0),
            entry.get("errors_count", 0),
        ))

    print("-" * 95)

    # Compare last vs baseline
    baseline = load_baseline()
    if baseline and len(history) >= 2:
        prev_data = history[-2]
        prev_snap = _snapshot_from_dict(prev_data)
        current_snap = _snapshot_from_dict(history[-1])
        report = compare_snapshots(prev_snap, current_snap)
        print("\n" + report.summary())
