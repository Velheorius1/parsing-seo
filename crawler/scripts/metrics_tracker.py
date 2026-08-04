#!/usr/bin/env python3
"""Metrics tracker for parsing-seo: snapshot, compare, trend.

Collects key metrics from Supabase (tenders, sources, alerts, contracts, feedback)
and saves snapshots to local JSONL for before/after comparison.

Usage:
    python3 -m crawler.scripts.metrics_tracker              # show metrics
    python3 -m crawler.scripts.metrics_tracker --save       # save snapshot
    python3 -m crawler.scripts.metrics_tracker --compare    # compare with last snapshot
    python3 -m crawler.scripts.metrics_tracker --telegram   # send to Telegram

Requires: supabase, httpx
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Where to save snapshots
METRICS_DIR = os.environ.get(
    "METRICS_DIR",
    "/opt/parsing-seo/logs" if os.path.exists("/opt/parsing-seo") else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs"
    ),
)
METRICS_FILE = os.path.join(METRICS_DIR, "metrics.jsonl")


def get_client():
    """Init Supabase client."""
    from crawler.config.settings import settings
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _fetch_all(client, table, select, filters=None, limit_per_page=1000):
    # type: (Any, str, str, Optional[List], int) -> List[Dict]
    """Fetch all rows with pagination (Supabase returns max 1000 per request).

    RESILIENT (2026-08-03 fix). Deep OFFSET pagination over `tenders` outgrew
    Supabase's statement timeout once the 7d window passed ~68k rows: the page at
    offset=68000 raised APIError 57014 and the exception propagated all the way out
    of main(), so THE DAILY SNAPSHOT WAS NEVER WRITTEN. It failed invisibly (the
    crawler shares this logfile, and cron logged a normal start), which silently
    broke week-over-week comparison — the weekly routine on 2026-08-03 found the
    07-27 snapshot sitting in the "this week" slot and scored `alerts_week: 0`.

    Now: retry each page, halving the page size on transient failures so a shorter
    range finishes inside the timeout. Never truncates silently — if a page still
    fails after the last retry the error is raised, exactly as before.
    """
    all_data = []  # type: List[Dict]
    offset = 0
    page = limit_per_page
    max_retries = 4
    while True:
        result = None
        for attempt in range(max_retries):
            q = client.table(table).select(select)
            if filters:
                for f in filters:
                    q = f(q)
            try:
                result = q.range(offset, offset + page - 1).execute()
                break
            except Exception as exc:
                if attempt == max_retries - 1:
                    logger.error(
                        "[_fetch_all] %s offset=%d page=%d failed after %d attempts: %s",
                        table, offset, page, max_retries, exc,
                    )
                    raise
                page = max(100, page // 2)
                logger.warning(
                    "[_fetch_all] %s offset=%d failed (attempt %d/%d): %s "
                    "— retrying with page=%d",
                    table, offset, attempt + 1, max_retries, exc, page,
                )
                time.sleep(2 ** attempt)
        if not result.data:
            break
        all_data.extend(result.data)
        if len(result.data) < page:
            break
        offset += len(result.data)
    return all_data


def _get_count(client, table, select="*", filters=None):
    # type: (Any, str, str, Optional[List]) -> int
    """Row count without fetching the rows. Exact when it fits the timeout.

    ВТОРАЯ ПОЛОВИНА того же дефекта, что чинил 742807b (2026-08-03). Тот фикс
    сделал устойчивой ПАГИНАЦИЮ `_fetch_all`, но счётчики остались как были, и
    суточный снапшот продолжил падать: трейс 04.08 00:00:12 —

        _get_count(client, "tenders") -> q.limit(0).execute()
        postgrest APIError 57014 canceling statement due to statement timeout

    `count="exact"` без фильтров — это COUNT(*) по всей таблице `tenders`, и он
    перерос statement timeout Supabase. Исключение вылетало из collect_metrics
    и валило main() целиком, ровно как раньше: снапшот за сутки не писался, а в
    логе это выглядело как обычный старт крона.

    Лечение — деградация, а не молчание: сначала точный счёт, при таймауте
    планировщиковая оценка (`planned` берётся из статистики Postgres и стоит
    милисекунды). Оценка помечается в логе WARNING, чтобы «total_tenders» с
    погрешностью нельзя было принять за точное число. Фильтрованные счётчики
    (сутки/неделя) узкие и почти всегда проходят точным путём.
    """
    def _run(count_mode):
        q = client.table(table).select(select, count=count_mode)
        if filters:
            for f in filters:
                q = f(q)
        return q.limit(0).execute()

    try:
        return _run("exact").count or 0
    except Exception as exc:
        if "57014" not in str(exc) and "statement timeout" not in str(exc):
            raise
        logger.warning(
            "[_get_count] %s: точный счёт не уложился в timeout (%s) — беру оценку",
            table, str(exc)[:80],
        )

    result = _run("planned")
    count = result.count or 0
    logger.warning("[_get_count] %s: ОЦЕНКА (planned) = %d, не точное число", table, count)
    return count


def collect_metrics(client):
    # type: (Any) -> Dict[str, Any]
    """Collect all key metrics from Supabase."""
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(days=1)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    metrics = {}  # type: Dict[str, Any]

    # 1. Total tenders
    metrics["total_tenders"] = _get_count(client, "tenders")
    logger.info("Total tenders: %d", metrics["total_tenders"])

    # 2. Tenders by source
    source_data = _fetch_all(client, "tenders", "source")
    source_counts = {}  # type: Dict[str, int]
    for row in source_data:
        src = row.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
    metrics["sources"] = source_counts
    metrics["total_sources"] = len(source_counts)
    logger.info("Sources: %d", len(source_counts))

    # 3. Tenders added in last 24h
    metrics["tenders_24h"] = _get_count(
        client, "tenders",
        filters=[lambda q: q.gte("collected_at", day_ago)]
    )
    logger.info("Tenders 24h: %d", metrics["tenders_24h"])

    # 4. Tenders added in last 7 days
    metrics["tenders_7d"] = _get_count(
        client, "tenders",
        filters=[lambda q: q.gte("collected_at", week_ago)]
    )
    logger.info("Tenders 7d: %d", metrics["tenders_7d"])

    # 5. Sources with 0 records in last 7 days (dead sources)
    recent_sources_data = _fetch_all(
        client, "tenders", "source",
        filters=[lambda q: q.gte("collected_at", week_ago)]
    )
    recent_sources = set()
    for row in recent_sources_data:
        recent_sources.add(row.get("source", ""))
    all_sources = set(source_counts.keys())
    dead_sources = sorted(all_sources - recent_sources)
    metrics["active_sources"] = len(recent_sources)
    metrics["dead_sources"] = dead_sources
    metrics["dead_sources_count"] = len(dead_sources)
    logger.info("Dead sources (7d): %d", len(dead_sources))

    # 6. Contracts with winner data (search_text contains 'winner:')
    metrics["contracts_with_winners"] = _get_count(
        client, "tenders",
        filters=[lambda q: q.like("search_text", "%winner:%")]
    )
    logger.info("Contracts with winners: %d", metrics["contracts_with_winners"])

    # 7. Contracts with discount data
    metrics["contracts_with_discounts"] = _get_count(
        client, "tenders",
        filters=[lambda q: q.like("search_text", "%discount:%")]
    )
    logger.info("Contracts with discounts: %d", metrics["contracts_with_discounts"])

    # 8. Total contracts (message_type = 'contract')
    try:
        metrics["total_contracts"] = _get_count(
            client, "tenders",
            filters=[lambda q: q.eq("message_type", "contract")]
        )
    except Exception:
        metrics["total_contracts"] = 0
    logger.info("Total contracts: %d", metrics["total_contracts"])

    # 9. Alert count (alert_seq not null)
    metrics["alerts_sent"] = _get_count(
        client, "tenders",
        filters=[lambda q: q.not_.is_("alert_seq", "null")]
    )
    logger.info("Alerts sent: %d", metrics["alerts_sent"])

    # 10. Feedback count
    try:
        metrics["feedback_count"] = _get_count(client, "alert_feedback")
    except Exception:
        metrics["feedback_count"] = 0
    logger.info("Feedback: %d", metrics["feedback_count"])

    # Timestamp
    metrics["timestamp"] = now.isoformat()
    metrics["date"] = now.strftime("%Y-%m-%d")

    return metrics


def format_metrics(metrics, prev_metrics=None):
    # type: (Dict[str, Any], Optional[Dict[str, Any]]) -> str
    """Format metrics as human-readable report."""

    def _delta(key, fmt="%+d"):
        # type: (str, str) -> str
        if prev_metrics and key in prev_metrics and key in metrics:
            diff = metrics[key] - prev_metrics[key]
            if diff != 0:
                return " (%s)" % (fmt % diff)
        return ""

    lines = [
        "=== PARSING-SEO METRICS (%s) ===" % metrics.get("date", "?"),
        "",
        "Total tenders: %s%s" % ("{:,}".format(metrics.get("total_tenders", 0)), _delta("total_tenders")),
        "Active sources: %d/%d (%d%%)" % (
            metrics.get("active_sources", 0),
            metrics.get("total_sources", 0),
            (metrics.get("active_sources", 0) * 100 // max(metrics.get("total_sources", 1), 1)),
        ),
        "Dead sources (7d): %d" % metrics.get("dead_sources_count", 0),
        "Contracts total: %s%s" % ("{:,}".format(metrics.get("total_contracts", 0)), _delta("total_contracts")),
        "Contracts with winners: %s%s" % ("{:,}".format(metrics.get("contracts_with_winners", 0)), _delta("contracts_with_winners")),
        "Contracts with discounts: %s%s" % ("{:,}".format(metrics.get("contracts_with_discounts", 0)), _delta("contracts_with_discounts")),
        "Tenders (24h): %s" % "{:,}".format(metrics.get("tenders_24h", 0)),
        "Tenders (7d): %s" % "{:,}".format(metrics.get("tenders_7d", 0)),
        "Alerts sent: %s%s" % ("{:,}".format(metrics.get("alerts_sent", 0)), _delta("alerts_sent")),
        "Feedback received: %s%s" % ("{:,}".format(metrics.get("feedback_count", 0)), _delta("feedback_count")),
    ]

    # Top 10 sources by count
    sources = metrics.get("sources", {})
    if sources:
        sorted_sources = sorted(sources.items(), key=lambda x: -x[1])[:10]
        lines.append("")
        lines.append("--- Top 10 sources ---")
        for src, count in sorted_sources:
            prev_count = (prev_metrics or {}).get("sources", {}).get(src)
            delta_str = ""
            if prev_count is not None:
                diff = count - prev_count
                if diff != 0:
                    delta_str = " (%+d)" % diff
            lines.append("  %-45s %s%s" % (src[:45], "{:,}".format(count), delta_str))

    # Dead sources list
    dead = metrics.get("dead_sources", [])
    if dead:
        lines.append("")
        lines.append("--- Dead sources (no data in 7d) ---")
        for src in dead[:20]:
            lines.append("  - %s" % src)
        if len(dead) > 20:
            lines.append("  ... and %d more" % (len(dead) - 20))

    return "\n".join(lines)


def save_snapshot(metrics):
    # type: (Dict[str, Any]) -> str
    """Save metrics snapshot to JSONL file."""
    os.makedirs(METRICS_DIR, exist_ok=True)
    with open(METRICS_FILE, "a") as f:
        f.write(json.dumps(metrics, ensure_ascii=False) + "\n")
    logger.info("Snapshot saved to %s", METRICS_FILE)
    return METRICS_FILE


def load_last_snapshot():
    # type: () -> Optional[Dict[str, Any]]
    """Load the most recent snapshot from JSONL."""
    if not os.path.exists(METRICS_FILE):
        return None
    last_line = None
    with open(METRICS_FILE, "r") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                last_line = stripped
    if last_line:
        try:
            return json.loads(last_line)
        except json.JSONDecodeError:
            return None
    return None


def load_snapshot_by_date(target_date):
    # type: (str) -> Optional[Dict[str, Any]]
    """Load snapshot closest to target_date (YYYY-MM-DD)."""
    if not os.path.exists(METRICS_FILE):
        return None
    best = None  # type: Optional[Dict[str, Any]]
    for line in open(METRICS_FILE, "r"):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
            if data.get("date") == target_date:
                best = data
        except json.JSONDecodeError:
            continue
    return best


def send_telegram(text):
    # type: (str) -> None
    """Send text to Telegram alert chat."""
    import httpx
    from crawler.config.settings import settings
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("Telegram not configured (no bot_token or chat_id)")
        return
    try:
        httpx.post(
            "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
            json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
            },
            timeout=10,
        )
        logger.info("Report sent to Telegram")
    except Exception as exc:
        logger.error("Failed to send Telegram: %s", str(exc)[:80])


def main():
    parser = argparse.ArgumentParser(description="Parsing-seo metrics tracker")
    parser.add_argument("--save", action="store_true", help="Save snapshot to JSONL")
    parser.add_argument("--compare", action="store_true", help="Compare with last snapshot")
    parser.add_argument("--date", type=str, help="Compare with snapshot from this date (YYYY-MM-DD)")
    parser.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    client = get_client()
    metrics = collect_metrics(client)

    # Load previous snapshot for comparison
    prev = None  # type: Optional[Dict[str, Any]]
    if args.compare or args.date:
        if args.date:
            prev = load_snapshot_by_date(args.date)
            if prev:
                logger.info("Comparing with snapshot from %s", prev.get("date"))
            else:
                logger.warning("No snapshot found for date %s", args.date)
        else:
            prev = load_last_snapshot()
            if prev:
                logger.info("Comparing with last snapshot from %s", prev.get("date"))
            else:
                logger.warning("No previous snapshot found in %s", METRICS_FILE)

    # Output
    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        report = format_metrics(metrics, prev)
        print(report)

    # Save
    if args.save:
        path = save_snapshot(metrics)
        print("\nSnapshot saved: %s" % path)

    # Telegram
    if args.telegram:
        report = format_metrics(metrics, prev)
        send_telegram(report)


if __name__ == "__main__":
    main()
