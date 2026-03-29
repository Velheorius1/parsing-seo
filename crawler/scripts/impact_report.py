#!/usr/bin/env python3
"""Impact assessment report for parsing-seo.

One-time or periodic report showing what's working, what's waste,
and where to invest effort next.

Analyzes:
1. Crawl runs — success rate, avg items per run
2. Source utilization — enabled vs actually producing data
3. Data freshness — avg age of most recent record per source
4. Coverage — unique organizations, regions
5. Contract insights — avg price, top buyers, winners
6. Feedback accuracy — client vs ad vs irrelevant ratios
7. Alert effectiveness — alerts sent vs feedback received

Usage:
    python3 -m crawler.scripts.impact_report                # full report
    python3 -m crawler.scripts.impact_report --section sources  # specific section
    python3 -m crawler.scripts.impact_report --json         # JSON output
    python3 -m crawler.scripts.impact_report --telegram     # send to Telegram

Requires: supabase, httpx
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_client():
    """Init Supabase client."""
    from crawler.config.settings import settings
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _fetch_all(client, table, select, filters=None, limit_per_page=1000):
    # type: (Any, str, str, Optional[List], int) -> List[Dict]
    """Fetch all rows with pagination."""
    all_data = []  # type: List[Dict]
    offset = 0
    while True:
        q = client.table(table).select(select)
        if filters:
            for f in filters:
                q = f(q)
        result = q.range(offset, offset + limit_per_page - 1).execute()
        if not result.data:
            break
        all_data.extend(result.data)
        if len(result.data) < limit_per_page:
            break
        offset += limit_per_page
    return all_data


def _get_count(client, table, select="*", filters=None):
    # type: (Any, str, str, Optional[List]) -> int
    """Get exact row count."""
    q = client.table(table).select(select, count="exact")
    if filters:
        for f in filters:
            q = f(q)
    result = q.limit(0).execute()
    return result.count or 0


# ── Section 1: Crawl Runs ──

def analyze_crawl_runs(client):
    # type: (Any) -> Dict[str, Any]
    """Analyze crawl_runs table: success rate, avg items."""
    report = {}  # type: Dict[str, Any]
    try:
        runs = _fetch_all(
            client, "crawl_runs",
            "id, started_at, finished_at, total_fetched, total_new, total_errors, status"
        )
    except Exception as exc:
        logger.warning("crawl_runs table not available: %s", str(exc)[:60])
        return {"error": "crawl_runs table not available"}

    if not runs:
        return {"total_runs": 0}

    report["total_runs"] = len(runs)

    # Success rate
    success = sum(1 for r in runs if r.get("status") == "completed")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    report["success_rate"] = "%.1f%%" % (success * 100.0 / max(len(runs), 1))
    report["completed"] = success
    report["failed"] = failed

    # Avg items per run
    fetched_list = [r.get("total_fetched", 0) or 0 for r in runs]
    new_list = [r.get("total_new", 0) or 0 for r in runs]
    error_list = [r.get("total_errors", 0) or 0 for r in runs]
    report["avg_fetched_per_run"] = sum(fetched_list) // max(len(fetched_list), 1)
    report["avg_new_per_run"] = sum(new_list) // max(len(new_list), 1)
    report["avg_errors_per_run"] = sum(error_list) // max(len(error_list), 1)
    report["total_fetched_all_time"] = sum(fetched_list)
    report["total_new_all_time"] = sum(new_list)

    # Last 7 days vs all time
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent_runs = []
    for r in runs:
        started = r.get("started_at", "")
        if started:
            try:
                dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                if dt > week_ago:
                    recent_runs.append(r)
            except Exception:
                pass
    report["runs_last_7d"] = len(recent_runs)
    recent_new = sum(r.get("total_new", 0) or 0 for r in recent_runs)
    report["new_last_7d"] = recent_new

    # Run duration (if finished_at available)
    durations = []
    for r in runs:
        started = r.get("started_at")
        finished = r.get("finished_at")
        if started and finished:
            try:
                dt_s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                dt_f = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                dur = (dt_f - dt_s).total_seconds()
                if 0 < dur < 7200:  # sanity: < 2h
                    durations.append(dur)
            except Exception:
                pass
    if durations:
        report["avg_duration_sec"] = int(sum(durations) / len(durations))
        report["max_duration_sec"] = int(max(durations))

    return report


# ── Section 2: Source Utilization ──

def analyze_source_utilization(client):
    # type: (Any) -> Dict[str, Any]
    """Enabled sources vs sources with data."""
    report = {}  # type: Dict[str, Any]
    now = datetime.now(timezone.utc)

    # All sources ever
    source_data = _fetch_all(client, "tenders", "source, collected_at")

    source_latest = {}  # type: Dict[str, str]
    source_counts = Counter()  # type: Counter
    for row in source_data:
        src = row.get("source", "unknown")
        source_counts[src] += 1
        ca = row.get("collected_at", "")
        if ca > source_latest.get(src, ""):
            source_latest[src] = ca

    report["total_sources_with_data"] = len(source_counts)
    report["total_records"] = sum(source_counts.values())

    # Freshness per source
    fresh_sources = []  # type: List[str]
    stale_sources = []  # type: List[Tuple[str, float]]
    for src, latest in source_latest.items():
        try:
            dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
            age_days = (now - dt).total_seconds() / 86400
            if age_days <= 7:
                fresh_sources.append(src)
            else:
                stale_sources.append((src, round(age_days, 1)))
        except Exception:
            stale_sources.append((src, -1))

    report["fresh_sources_7d"] = len(fresh_sources)
    report["stale_sources"] = sorted(stale_sources, key=lambda x: -x[1])

    # Source distribution (top + bottom)
    sorted_sources = source_counts.most_common()
    report["top_sources"] = sorted_sources[:15]
    report["bottom_sources"] = sorted_sources[-10:] if len(sorted_sources) > 15 else []

    # Concentration: what % of data comes from top 5 sources?
    top5_count = sum(c for _, c in sorted_sources[:5])
    total = sum(source_counts.values())
    report["top5_concentration"] = "%.1f%%" % (top5_count * 100.0 / max(total, 1))

    return report


# ── Section 3: Coverage ──

def analyze_coverage(client):
    # type: (Any) -> Dict[str, Any]
    """Unique organizations, regions."""
    report = {}  # type: Dict[str, Any]

    # Organizations
    org_data = _fetch_all(
        client, "tenders", "organization",
        filters=[
            lambda q: q.not_.is_("organization", "null"),
            lambda q: q.neq("organization", ""),
        ]
    )
    orgs = set()
    for row in org_data:
        org = (row.get("organization") or "").strip()
        if org and len(org) >= 3:
            orgs.add(org)
    report["unique_organizations"] = len(orgs)

    # Regions
    region_data = _fetch_all(
        client, "tenders", "region",
        filters=[
            lambda q: q.not_.is_("region", "null"),
            lambda q: q.neq("region", ""),
        ]
    )
    region_counts = Counter()  # type: Counter
    for row in region_data:
        reg = (row.get("region") or "").strip()
        if reg:
            region_counts[reg] += 1
    report["unique_regions"] = len(region_counts)
    report["regions"] = region_counts.most_common(20)

    return report


# ── Section 4: Contract Insights ──

def analyze_contracts(client):
    # type: (Any) -> Dict[str, Any]
    """Contract data: avg price, top buyers, winners."""
    report = {}  # type: Dict[str, Any]

    try:
        contracts = _fetch_all(
            client, "tenders",
            "title, organization, price, source, search_text, winner, winning_price",
            filters=[lambda q: q.eq("message_type", "contract")]
        )
    except Exception:
        contracts = _fetch_all(
            client, "tenders",
            "title, organization, price, source, search_text",
            filters=[lambda q: q.like("search_text", "%winner:%")]
        )

    if not contracts:
        return {"total": 0}

    report["total"] = len(contracts)

    # Prices
    prices = []
    for c in contracts:
        p = c.get("price") or c.get("winning_price")
        if p and float(p) > 0:
            prices.append(float(p))
    if prices:
        report["avg_price"] = "{:,.0f}".format(sum(prices) / len(prices))
        report["min_price"] = "{:,.0f}".format(min(prices))
        report["max_price"] = "{:,.0f}".format(max(prices))
        report["total_value"] = "{:,.0f}".format(sum(prices))
        report["contracts_with_price"] = len(prices)

    # Top buyers
    buyer_counts = Counter()  # type: Counter
    buyer_values = {}  # type: Dict[str, float]
    for c in contracts:
        buyer = (c.get("organization") or "").strip()
        if buyer:
            buyer_counts[buyer] += 1
            p = float(c.get("price", 0) or 0)
            buyer_values[buyer] = buyer_values.get(buyer, 0) + p
    report["top_buyers_by_count"] = buyer_counts.most_common(10)
    top_by_value = sorted(buyer_values.items(), key=lambda x: -x[1])[:10]
    report["top_buyers_by_value"] = [
        (b, "{:,.0f}".format(v)) for b, v in top_by_value
    ]

    # Winners (from winner field or search_text)
    winner_counts = Counter()  # type: Counter
    for c in contracts:
        winner = c.get("winner", "")
        if not winner:
            # Try extract from search_text
            st = c.get("search_text", "")
            if "winner:" in st:
                parts = st.split("winner:")
                if len(parts) > 1:
                    winner = parts[1].split("discount:")[0].strip()
        if winner and len(winner) >= 3:
            winner_counts[winner] += 1
    report["unique_winners"] = len(winner_counts)
    report["top_winners"] = winner_counts.most_common(10)

    # Discount stats
    discounts = []
    for c in contracts:
        st = c.get("search_text", "")
        if "discount:" in st:
            parts = st.split("discount:")
            if len(parts) > 1:
                try:
                    disc_str = parts[1].strip().split()[0].replace("%", "")
                    disc = float(disc_str)
                    if 0 < disc < 100:
                        discounts.append(disc)
                except (ValueError, IndexError):
                    pass
    if discounts:
        report["avg_discount"] = "%.1f%%" % (sum(discounts) / len(discounts))
        report["min_discount"] = "%.1f%%" % min(discounts)
        report["max_discount"] = "%.1f%%" % max(discounts)
        report["contracts_with_discount"] = len(discounts)

    return report


# ── Section 5: Feedback Analysis ──

def analyze_feedback(client):
    # type: (Any) -> Dict[str, Any]
    """Alert feedback: accuracy of AI filtering."""
    report = {}  # type: Dict[str, Any]

    try:
        feedback = _fetch_all(client, "alert_feedback", "label, created_at")
    except Exception as exc:
        logger.warning("alert_feedback not available: %s", str(exc)[:60])
        return {"error": "alert_feedback table not available"}

    if not feedback:
        return {"total": 0}

    report["total"] = len(feedback)

    label_counts = Counter()  # type: Counter
    for f in feedback:
        label = f.get("label", "unknown")
        label_counts[label] += 1

    report["labels"] = dict(label_counts.most_common())

    # Calculate accuracy (client = relevant, everything else = noise)
    client_count = label_counts.get("client", 0) + label_counts.get("relevant", 0)
    total = len(feedback)
    report["relevance_rate"] = "%.1f%%" % (client_count * 100.0 / max(total, 1))

    # Alerts sent total
    alerts_sent = _get_count(
        client, "tenders",
        filters=[lambda q: q.not_.is_("alert_seq", "null")]
    )
    report["alerts_sent"] = alerts_sent
    report["feedback_rate"] = "%.1f%%" % (total * 100.0 / max(alerts_sent, 1))

    return report


# ── Section 6: Alert Effectiveness ──

def analyze_alerts(client):
    # type: (Any) -> Dict[str, Any]
    """Alert volume and timing."""
    report = {}  # type: Dict[str, Any]

    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()

    report["total_alerts"] = _get_count(
        client, "tenders",
        filters=[lambda q: q.not_.is_("alert_seq", "null")]
    )
    report["alerts_7d"] = _get_count(
        client, "tenders",
        filters=[
            lambda q: q.not_.is_("alert_seq", "null"),
            lambda q: q.gte("collected_at", week_ago),
        ]
    )

    return report


# ── Formatting ──

def format_report(sections):
    # type: (Dict[str, Dict[str, Any]]) -> str
    """Format all sections into readable report."""
    lines = [
        "=" * 60,
        "  PARSING-SEO IMPACT ASSESSMENT",
        "  %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "=" * 60,
    ]

    # Crawl Runs
    if "crawl_runs" in sections:
        cr = sections["crawl_runs"]
        lines.append("")
        lines.append("1. CRAWL RUNS")
        lines.append("-" * 40)
        if cr.get("error"):
            lines.append("  %s" % cr["error"])
        elif cr.get("total_runs", 0) == 0:
            lines.append("  No crawl runs recorded")
        else:
            lines.append("  Total runs: %d (success: %s)" % (cr["total_runs"], cr.get("success_rate", "?")))
            lines.append("  Completed: %d, Failed: %d" % (cr.get("completed", 0), cr.get("failed", 0)))
            lines.append("  Avg fetched/run: %d, Avg new/run: %d" % (
                cr.get("avg_fetched_per_run", 0), cr.get("avg_new_per_run", 0)))
            lines.append("  Avg errors/run: %d" % cr.get("avg_errors_per_run", 0))
            if cr.get("avg_duration_sec"):
                lines.append("  Avg duration: %ds, Max: %ds" % (
                    cr.get("avg_duration_sec", 0), cr.get("max_duration_sec", 0)))
            lines.append("  Last 7d: %d runs, %d new tenders" % (
                cr.get("runs_last_7d", 0), cr.get("new_last_7d", 0)))

    # Source Utilization
    if "sources" in sections:
        su = sections["sources"]
        lines.append("")
        lines.append("2. SOURCE UTILIZATION")
        lines.append("-" * 40)
        lines.append("  Sources with data: %d" % su.get("total_sources_with_data", 0))
        lines.append("  Fresh (7d): %d" % su.get("fresh_sources_7d", 0))
        lines.append("  Stale: %d" % len(su.get("stale_sources", [])))
        lines.append("  Top 5 concentration: %s of all data" % su.get("top5_concentration", "?"))
        lines.append("")
        lines.append("  Top sources:")
        for src, count in su.get("top_sources", []):
            lines.append("    %-42s %s" % (src[:42], "{:,}".format(count)))
        stale = su.get("stale_sources", [])
        if stale:
            lines.append("")
            lines.append("  Stale sources (days since last data):")
            for src, days in stale[:10]:
                lines.append("    %-42s %.0fd" % (src[:42], days))

    # Coverage
    if "coverage" in sections:
        cv = sections["coverage"]
        lines.append("")
        lines.append("3. COVERAGE")
        lines.append("-" * 40)
        lines.append("  Unique organizations: %s" % "{:,}".format(cv.get("unique_organizations", 0)))
        lines.append("  Unique regions: %d" % cv.get("unique_regions", 0))
        regions = cv.get("regions", [])
        if regions:
            lines.append("  Region distribution:")
            for reg, count in regions:
                lines.append("    %-35s %s" % (reg[:35], "{:,}".format(count)))

    # Contracts
    if "contracts" in sections:
        ct = sections["contracts"]
        lines.append("")
        lines.append("4. CONTRACT INSIGHTS")
        lines.append("-" * 40)
        if ct.get("total", 0) == 0:
            lines.append("  No contract data yet")
        else:
            lines.append("  Total contracts: %d" % ct["total"])
            if ct.get("contracts_with_price"):
                lines.append("  With price: %d" % ct["contracts_with_price"])
                lines.append("  Avg price: %s UZS" % ct.get("avg_price", "?"))
                lines.append("  Total value: %s UZS" % ct.get("total_value", "?"))
            if ct.get("unique_winners"):
                lines.append("  Unique winners: %d" % ct["unique_winners"])
            if ct.get("avg_discount"):
                lines.append("  Avg discount: %s (n=%d)" % (
                    ct["avg_discount"], ct.get("contracts_with_discount", 0)))

            top_winners = ct.get("top_winners", [])
            if top_winners:
                lines.append("")
                lines.append("  Top winners:")
                for w, c in top_winners:
                    lines.append("    %-42s %d contracts" % (w[:42], c))

            top_buyers = ct.get("top_buyers_by_count", [])
            if top_buyers:
                lines.append("")
                lines.append("  Top buyers:")
                for b, c in top_buyers:
                    lines.append("    %-42s %d contracts" % (b[:42], c))

    # Feedback
    if "feedback" in sections:
        fb = sections["feedback"]
        lines.append("")
        lines.append("5. FEEDBACK & ACCURACY")
        lines.append("-" * 40)
        if fb.get("error"):
            lines.append("  %s" % fb["error"])
        elif fb.get("total", 0) == 0:
            lines.append("  No feedback yet")
        else:
            lines.append("  Alerts sent: %s" % "{:,}".format(fb.get("alerts_sent", 0)))
            lines.append("  Feedback received: %d (%s of alerts)" % (
                fb["total"], fb.get("feedback_rate", "?")))
            lines.append("  Relevance rate: %s" % fb.get("relevance_rate", "?"))
            labels = fb.get("labels", {})
            if labels:
                lines.append("  Labels:")
                for label, count in sorted(labels.items(), key=lambda x: -x[1]):
                    lines.append("    %-20s %d" % (label, count))

    # Alerts
    if "alerts" in sections:
        al = sections["alerts"]
        lines.append("")
        lines.append("6. ALERT VOLUME")
        lines.append("-" * 40)
        lines.append("  Total alerts: %s" % "{:,}".format(al.get("total_alerts", 0)))
        lines.append("  Alerts (7d): %s" % "{:,}".format(al.get("alerts_7d", 0)))

    # Summary verdict
    lines.append("")
    lines.append("=" * 60)
    lines.append("  VERDICT")
    lines.append("=" * 60)
    _add_verdict(lines, sections)

    return "\n".join(lines)


def _add_verdict(lines, sections):
    # type: (List[str], Dict[str, Dict[str, Any]]) -> None
    """Generate actionable verdict."""
    issues = []  # type: List[str]
    strengths = []  # type: List[str]

    su = sections.get("sources", {})
    stale = su.get("stale_sources", [])
    fresh = su.get("fresh_sources_7d", 0)
    total_src = su.get("total_sources_with_data", 0)

    if stale:
        issues.append("  - %d stale sources (no data >7d) — disable or fix" % len(stale))
    if fresh > 0:
        strengths.append("  + %d/%d sources actively producing data" % (fresh, total_src))

    cr = sections.get("crawl_runs", {})
    if cr.get("avg_errors_per_run", 0) > 5:
        issues.append("  - High error rate: avg %d errors/run" % cr["avg_errors_per_run"])
    if cr.get("total_runs", 0) > 0:
        strengths.append("  + %d crawl runs logged" % cr["total_runs"])

    fb = sections.get("feedback", {})
    if fb.get("total", 0) > 0:
        strengths.append("  + Feedback loop active (%d responses)" % fb["total"])
    else:
        issues.append("  - No feedback data — AI filtering unvalidated")

    ct = sections.get("contracts", {})
    if ct.get("total", 0) > 0:
        strengths.append("  + %d contracts with competitive intelligence" % ct["total"])
    else:
        issues.append("  - No contract data — missing winner/price insights")

    if strengths:
        lines.append("  WORKING:")
        lines.extend(strengths)
    if issues:
        lines.append("  NEEDS ATTENTION:")
        lines.extend(issues)
    if not issues:
        lines.append("  All systems nominal.")


def send_telegram(text):
    # type: (str) -> None
    """Send text to Telegram alert chat."""
    import httpx
    from crawler.config.settings import settings
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("Telegram not configured")
        return

    # Split if > 4096 chars
    chunks = []
    while len(text) > 4000:
        split_at = text.rfind("\n", 0, 4000)
        if split_at == -1:
            split_at = 4000
        chunks.append(text[:split_at])
        text = text[split_at:]
    chunks.append(text)

    for chunk in chunks:
        try:
            httpx.post(
                "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                json={
                    "chat_id": settings.telegram_alert_chat_id,
                    "text": chunk,
                },
                timeout=10,
            )
        except Exception as exc:
            logger.error("Telegram send failed: %s", str(exc)[:80])

    logger.info("Report sent to Telegram (%d parts)" % len(chunks))


def main():
    parser = argparse.ArgumentParser(description="Parsing-seo impact report")
    parser.add_argument("--section", type=str, help="Run specific section only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--telegram", action="store_true", help="Send to Telegram")
    args = parser.parse_args()

    client = get_client()
    sections = {}  # type: Dict[str, Dict[str, Any]]

    section_map = {
        "crawl_runs": ("crawl_runs", lambda: analyze_crawl_runs(client)),
        "sources": ("sources", lambda: analyze_source_utilization(client)),
        "coverage": ("coverage", lambda: analyze_coverage(client)),
        "contracts": ("contracts", lambda: analyze_contracts(client)),
        "feedback": ("feedback", lambda: analyze_feedback(client)),
        "alerts": ("alerts", lambda: analyze_alerts(client)),
    }

    if args.section:
        if args.section in section_map:
            key, fn = section_map[args.section]
            logger.info("Running section: %s", args.section)
            sections[key] = fn()
        else:
            print("Unknown section: %s. Available: %s" % (
                args.section, ", ".join(section_map.keys())))
            sys.exit(1)
    else:
        for name, (key, fn) in section_map.items():
            logger.info("Analyzing: %s", name)
            sections[key] = fn()

    if args.json:
        # Make JSON-serializable
        def _serialize(obj):
            if isinstance(obj, set):
                return list(obj)
            return str(obj)
        print(json.dumps(sections, indent=2, ensure_ascii=False, default=_serialize))
    else:
        report = format_report(sections)
        print(report)

    if args.telegram:
        report = format_report(sections)
        send_telegram(report)


if __name__ == "__main__":
    main()
