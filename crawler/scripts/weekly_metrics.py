"""Weekly metrics collector for the self-improvement routine.

Produces a deterministic JSON artifact comparing THIS week vs LAST week so the
weekly Claude routine (which runs /deep-think) has reliable, reproducible numbers
to score and reason over — instead of re-deriving them ad-hoc each run.

What it computes (all from data already on the VPS, no new infra):
  - week-over-week: alerts, active/dead sources, volume, feedback (logs/metrics.jsonl)
  - AI reliability: calls / error% / p95 latency / relevant-pass (ai-decisions.jsonl)
  - feedback corrections (crawler.core.feedback.get_feedback_stats)
  - source-health prune CANDIDATES (report-only — never disables anything)
  - 4 deterministic sub-scores (precision, platform, recall, cost) on a fixed rubric
  - link-integrity is left for the routine's browser check (a sample is emitted)

The composite 0-10 and improvement ideas are finalized by the weekly Claude routine
(it adds the browser-verified link-integrity sub-score + /deep-think). Keeping the
data deterministic here makes week-over-week scores comparable; keeping the thinking
in Claude makes the improvement loop real.

Usage:
    python3 -m crawler.scripts.weekly_metrics            # print JSON to stdout
    python3 -m crawler.scripts.weekly_metrics --save     # also write docs/weekly/data/<week>.json

Python 3.9 compatible (no match/case, no X|Y unions).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
METRICS_JSONL = os.path.join(REPO_ROOT, "logs", "metrics.jsonl")
AI_LOG = "/var/log/parsing-seo-ai-decisions.jsonl"
OUT_DIR = os.path.join(REPO_ROOT, "docs", "weekly", "data")

# Sub-score rubric weights (fixed convention for week-over-week comparability).
# link_integrity is added by the weekly routine after its browser check.
WEIGHTS = {
    "link_integrity": 0.25,
    "precision": 0.25,
    "platform_health": 0.20,
    "recall": 0.15,
    "cost_reliability": 0.15,
}

# Sources whose deep-link route is statically known-good after the 2026-06-21 fix.
# Used only to emit a browser-check sample, NOT to score blindly.
LINK_SAMPLE_SOURCES = [
    "UZEX Предквалификации",
    "UZEX Обратные аукционы",
    "UZEX Э-магазин издательские услуги",
    "XT-Xarid встречные аукционы",
    "ETender UZEX",
    "Cooperation.uz Лоты",
]


def _load_snapshots():
    # type: () -> List[dict]
    snaps = []  # type: List[dict]
    try:
        with open(METRICS_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    snaps.append(json.loads(line))
                except Exception:
                    pass
    except IOError:
        pass
    return snaps


def _nearest(snaps, target_date):
    # type: (List[dict], object) -> Optional[dict]
    best = None
    best_diff = 10 ** 9
    for s in snaps:
        d = s.get("date")
        if not d:
            continue
        try:
            sd = datetime.fromisoformat(d).date()
        except Exception:
            continue
        diff = abs((sd - target_date).days)
        if diff < best_diff:
            best_diff = diff
            best = s
    return best


def _ai_window(start, end):
    # type: (datetime, datetime) -> dict
    n = 0
    err = 0
    rel = 0
    lats = []  # type: List[float]
    try:
        with open(AI_LOG) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = r.get("ts")
                if not ts:
                    continue
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if start <= t < end:
                    n += 1
                    http = r.get("http_status")
                    if r.get("error") or (http is not None and http != 200):
                        err += 1
                    lat = r.get("latency_ms")
                    if isinstance(lat, (int, float)):
                        lats.append(lat)
                    if r.get("is_relevant"):
                        rel += 1
    except IOError:
        return {"calls": 0, "err_pct": 0.0, "p95_ms": 0, "relevant": 0}
    lats.sort()
    p95 = lats[int(len(lats) * 0.95)] if lats else 0
    return {
        "calls": n,
        "err_pct": round(err / n * 100, 2) if n else 0.0,
        "p95_ms": p95,
        "relevant": rel,
    }


def _persistent_dead(snaps, weeks=3):
    # type: (List[dict], int) -> List[str]
    """Sources that appear in dead_sources across the last `weeks` weekly snapshots."""
    today = datetime.now(timezone.utc).date()
    weekly = []
    for w in range(weeks):
        s = _nearest(snaps, today - timedelta(days=7 * w))
        if s:
            weekly.append(set(s.get("dead_sources", []) or []))
    if not weekly:
        return []
    persistent = set(weekly[0])
    for ds in weekly[1:]:
        persistent &= ds
    return sorted(persistent)


def _subscore_precision(fb7):
    # type: (dict) -> Tuple[float, str]
    total = fb7.get("total", 0) or 0
    if total == 0:
        # No production feedback -> precision UNVERIFIED. Conservative proxy.
        return 8.0, "PROXY: 0 feedback this week, FP-rate unverified; classifier golden ~95-100%"
    fp = fb7.get("false_positives", 0) or 0
    return round(max(0.0, (1.0 - fp / total)) * 10, 1), "feedback-based: %d FP / %d total" % (fp, total)


def _subscore_platform(this_wk):
    # type: (dict) -> Tuple[float, str]
    active = this_wk.get("active_sources") or 0
    total = this_wk.get("total_sources") or 0
    if not total:
        return 0.0, "no source count"
    frac = active / total
    return round(frac * 10, 1), "active %d / %d = %.0f%%" % (active, total, frac * 100)


def _subscore_recall(this_wk, last_wk):
    # type: (dict, dict) -> Tuple[float, str]
    a_now = this_wk.get("alerts_week") or 0
    a_prev = last_wk.get("alerts_week") or 0
    # baseline coverage audit anchor = 6.8/10; modulate by WoW alert stability
    base = 6.8
    if a_prev:
        ratio = a_now / a_prev
        if ratio < 0.7:
            base -= 1.0
        elif ratio > 1.3:
            base += 0.5
    return round(max(0.0, min(10.0, base)), 1), "alerts %d vs %d (anchor 6.8)" % (a_now, a_prev)


def _subscore_cost(ai_now):
    # type: (dict) -> Tuple[float, str]
    err = ai_now.get("err_pct", 0.0)
    p95 = ai_now.get("p95_ms", 0)
    # error component (0.4% -> ~9.6), latency component (target <6s -> 10, >16s -> ~5)
    err_pts = max(0.0, 10.0 - err * 2.0)
    if p95 <= 6000:
        lat_pts = 10.0
    elif p95 >= 16000:
        lat_pts = 5.0
    else:
        lat_pts = 10.0 - (p95 - 6000) / 10000.0 * 5.0
    score = round(err_pts * 0.4 + lat_pts * 0.6, 1)
    return score, "err %.2f%% (%.1f) + p95 %dms (%.1f)" % (err, err_pts, p95, lat_pts)


def collect():
    # type: () -> dict
    snaps = _load_snapshots()
    today = datetime.now(timezone.utc).date()
    s_now = _nearest(snaps, today)
    s_7 = _nearest(snaps, today - timedelta(days=7))
    s_14 = _nearest(snaps, today - timedelta(days=14))

    def wk(cur, prev):
        # alerts in the window = delta of cumulative alerts_sent
        a = None
        if cur and prev and cur.get("alerts_sent") is not None and prev.get("alerts_sent") is not None:
            a = cur["alerts_sent"] - prev["alerts_sent"]
        return {
            "date": cur.get("date") if cur else None,
            "alerts_sent_cum": cur.get("alerts_sent") if cur else None,
            "alerts_week": a,
            "active_sources": cur.get("active_sources") if cur else None,
            "total_sources": cur.get("total_sources") if cur else None,
            "dead_sources": cur.get("dead_sources_count") if cur else None,
            "tenders_7d": cur.get("tenders_7d") if cur else None,
            "feedback_cum": cur.get("feedback_count") if cur else None,
        }

    this_wk = wk(s_now, s_7)
    last_wk = wk(s_7, s_14)

    now = datetime.now(timezone.utc)
    ai_now = _ai_window(now - timedelta(days=7), now)
    ai_prev = _ai_window(now - timedelta(days=14), now - timedelta(days=7))
    this_wk["ai"] = ai_now
    last_wk["ai"] = ai_prev

    try:
        from crawler.core.feedback import get_feedback_stats
        fb7 = get_feedback_stats(7)
        fb30 = get_feedback_stats(30)
    except Exception as e:
        fb7 = {"total": 0, "error": repr(e)[:120]}
        fb30 = {"total": 0}

    # deterministic sub-scores (link_integrity added by the routine)
    sp, sp_why = _subscore_precision(fb7)
    pl, pl_why = _subscore_platform(this_wk)
    rc, rc_why = _subscore_recall(this_wk, last_wk)
    co, co_why = _subscore_cost(ai_now)
    subscores = {
        "precision": {"score": sp, "why": sp_why},
        "platform_health": {"score": pl, "why": pl_why},
        "recall": {"score": rc, "why": rc_why},
        "cost_reliability": {"score": co, "why": co_why},
    }

    # prune candidates (REPORT-ONLY): dead across last 3 weekly snapshots
    persistent_dead = _persistent_dead(snaps, weeks=3)
    sources_now = (s_now or {}).get("sources", {}) or {}
    prune = []
    for name in persistent_dead:
        prune.append({
            "source": name,
            "reason": "no new data in dead_sources across last 3 weekly snapshots",
            "total_rows": sources_now.get(name),
        })

    iso = today.isocalendar()
    iso_week = "%04d-W%02d" % (iso[0], iso[1])

    return {
        "iso_week": iso_week,
        "this_week": this_wk,
        "last_week": last_wk,
        "feedback": {"last_7d": fb7, "last_30d": fb30},
        "subscores_deterministic": subscores,
        "weights": WEIGHTS,
        "link_integrity": {
            "needs_browser_check": True,
            "instructions": "Navigate 6-10 of the sample URLs; link-integrity = % that render a valid lot. Add as the 25%% sub-score, then composite = sum(weight*score).",
            "sample_sources": LINK_SAMPLE_SOURCES,
        },
        "prune_candidates_report_only": prune,
        "notes": [
            "Composite + improvement ideas finalized by the weekly Claude routine (it runs /deep-think and the browser link-check).",
            "prune_candidates are REPORT-ONLY: nothing is auto-disabled.",
            "alerts_week = delta of cumulative alerts_sent between weekly snapshots (today snapshot may be mid-day).",
        ],
    }


def main():
    # type: () -> int
    save = "--save" in sys.argv
    data = collect()
    out = json.dumps(data, ensure_ascii=False, indent=2)
    if save:
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            path = os.path.join(OUT_DIR, "%s.json" % data["iso_week"])
            with open(path, "w") as f:
                f.write(out)
            sys.stderr.write("[weekly_metrics] saved %s\n" % path)
        except IOError as e:
            sys.stderr.write("[weekly_metrics] save failed: %s\n" % e)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
