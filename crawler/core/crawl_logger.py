"""Persistent crawl run logger — writes per-run stats to JSONL + Supabase."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# JSONL log path (inside container: /app/crawler/logs/)
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_RUNS_LOG = os.path.join(_LOG_DIR, "crawl_runs.jsonl")
_DECISIONS_LOG = os.path.join(_LOG_DIR, "crawl_decisions.jsonl")


class SourceStats:
    """Per-source stats within a single crawl run."""

    __slots__ = ("source_id", "fetched", "new", "errors", "duration_ms", "started_at")

    def __init__(self, source_id):
        # type: (str) -> None
        self.source_id = source_id
        self.fetched = 0
        self.new = 0
        self.errors = []  # type: List[str]
        self.duration_ms = 0.0
        self.started_at = 0.0

    def to_dict(self):
        # type: () -> Dict
        return {
            "fetched": self.fetched,
            "new": self.new,
            "errors": self.errors,
            "duration_ms": round(self.duration_ms, 1),
        }


class CrawlRunLogger:
    """Collects metrics during crawl pipeline and persists them."""

    def __init__(self, dry_run=False, source_filter=None):
        # type: (bool, Optional[List[str]]) -> None
        self.dry_run = dry_run
        self.source_filter = source_filter
        self.started_at = datetime.now(timezone.utc)
        self._start_mono = time.monotonic()
        self._source_stats = {}  # type: Dict[str, SourceStats]
        self.total_upserted = 0
        self.total_new = 0
        self.enriched_count = 0
        self.ai_calls = 0
        self.alerts_sent = 0
        self.errors = []  # type: List[str]

    def log_source_start(self, source_id):
        # type: (str) -> None
        stats = SourceStats(source_id)
        stats.started_at = time.monotonic()
        self._source_stats[source_id] = stats

    def log_source_result(self, source_id, fetched, error=None):
        # type: (str, int, Optional[str]) -> None
        stats = self._source_stats.get(source_id)
        if not stats:
            stats = SourceStats(source_id)
            self._source_stats[source_id] = stats
        stats.fetched = fetched
        stats.duration_ms = (time.monotonic() - stats.started_at) * 1000
        if error:
            stats.errors.append(error[:200])
            self.errors.append("[%s] %s" % (source_id, error[:200]))

    def log_upsert(self, upserted, new_count):
        # type: (int, int) -> None
        self.total_upserted = upserted
        self.total_new = new_count

    def log_enrichment(self, enriched, ai_calls=0):
        # type: (int, int) -> None
        self.enriched_count = enriched
        self.ai_calls += ai_calls

    def log_alerts(self, sent):
        # type: (int) -> None
        self.alerts_sent = sent

    def log_ai_call(self, count=1):
        # type: (int) -> None
        self.ai_calls += count

    async def finalize(self):
        # type: () -> None
        """Persist run stats to JSONL file and optionally to Supabase."""
        finished_at = datetime.now(timezone.utc)
        duration = time.monotonic() - self._start_mono

        total_fetched = sum(s.fetched for s in self._source_stats.values())
        error_sources = [
            sid for sid, s in self._source_stats.items() if s.errors
        ]

        run_data = {
            "started_at": self.started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round(duration, 1),
            "total_fetched": total_fetched,
            "total_new": self.total_new,
            "total_upserted": self.total_upserted,
            "total_enriched": self.enriched_count,
            "alerts_sent": self.alerts_sent,
            "errors_count": len(self.errors),
            "source_details": {
                sid: s.to_dict() for sid, s in self._source_stats.items()
            },
            "ai_calls_count": self.ai_calls,
            "ai_estimated_cost_usd": round(self.ai_calls * 0.00003, 5),
            "error_sources": error_sources,
            "error_messages": self.errors[:20],
            "dry_run": self.dry_run,
            "source_filter": self.source_filter,
        }

        # Write to JSONL file (always — works even if Supabase is down)
        self._write_jsonl(run_data)

        # Write to Supabase (if configured and not dry_run)
        if not self.dry_run and settings.supabase_url and settings.supabase_service_role_key:
            self._write_supabase(run_data)

        logger.info(
            "Crawl run logged: %d fetched, %d new, %d upserted, %d enriched, "
            "%d alerts, %d errors, %.1fs",
            total_fetched, self.total_new, self.total_upserted,
            self.enriched_count, self.alerts_sent, len(self.errors), duration,
        )

    def _write_jsonl(self, data):
        # type: (Dict) -> None
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            with open(_RUNS_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Failed to write crawl log to JSONL: %s", str(exc)[:100])

    def _write_supabase(self, data):
        # type: (Dict) -> None
        try:
            from supabase import create_client
            client = create_client(settings.supabase_url, settings.supabase_service_role_key)
            client.table("crawl_runs").insert({
                "started_at": data["started_at"],
                "finished_at": data["finished_at"],
                "duration_seconds": data["duration_seconds"],
                "total_fetched": data["total_fetched"],
                "total_new": data["total_new"],
                "total_upserted": data["total_upserted"],
                "total_enriched": data["total_enriched"],
                "alerts_sent": data["alerts_sent"],
                "errors_count": data["errors_count"],
                "source_details": data["source_details"],
                "ai_calls_count": data["ai_calls_count"],
                "ai_estimated_cost_usd": data["ai_estimated_cost_usd"],
                "error_sources": data["error_sources"],
                "error_messages": data["error_messages"],
                "dry_run": data["dry_run"],
                "source_filter": data["source_filter"],
            }).execute()
        except Exception as exc:
            logger.warning("Failed to write crawl run to Supabase: %s", str(exc)[:100])


def print_stats(last_n=10):
    # type: (int) -> None
    """Print last N crawl runs from JSONL log (CLI --stats command)."""
    if not os.path.exists(_RUNS_LOG):
        print("No crawl runs logged yet. Run the crawler first.")
        return

    runs = []
    with open(_RUNS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not runs:
        print("No crawl runs found in log.")
        return

    runs = runs[-last_n:]

    print("\n=== Last %d Crawl Runs ===" % len(runs))
    print("-" * 90)
    print("%-20s %6s %5s %5s %5s %5s %5s %6s" % (
        "Started", "Fetch", "New", "Ups", "Enr", "Alert", "Err", "Dur(s)",
    ))
    print("-" * 90)

    for r in runs:
        started = r.get("started_at", "?")[:19]
        print("%-20s %6d %5d %5d %5d %5d %5d %6.1f" % (
            started,
            r.get("total_fetched", 0),
            r.get("total_new", 0),
            r.get("total_upserted", 0),
            r.get("total_enriched", 0),
            r.get("alerts_sent", 0),
            r.get("errors_count", 0),
            r.get("duration_seconds", 0),
        ))

    print("-" * 90)

    # Show error sources from last run
    last = runs[-1]
    if last.get("error_sources"):
        print("\nError sources (last run): %s" % ", ".join(last["error_sources"]))

    # Show per-source breakdown from last run
    details = last.get("source_details", {})
    if details:
        print("\n--- Last Run Per-Source ---")
        for sid, info in sorted(details.items(), key=lambda x: -x[1].get("fetched", 0)):
            fetched = info.get("fetched", 0)
            dur = info.get("duration_ms", 0)
            errs = info.get("errors", [])
            status = "ERR" if errs else "OK"
            print("  %-25s %5d tenders  %6.0fms  [%s]" % (sid, fetched, dur, status))


def print_source_stats(source_id, days=7):
    # type: (str, int) -> None
    """Print stats for a specific source over last N days."""
    if not os.path.exists(_RUNS_LOG):
        print("No crawl runs logged yet.")
        return

    runs = []
    with open(_RUNS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not runs:
        print("No crawl runs found.")
        return

    # Filter runs with this source
    relevant = []
    for r in runs:
        details = r.get("source_details", {})
        if source_id in details:
            relevant.append((r["started_at"][:19], details[source_id]))

    if not relevant:
        print("No data for source '%s'" % source_id)
        return

    relevant = relevant[-(days * 12):]  # ~12 runs/day (every 2h)

    print("\n=== Source: %s (last %d runs) ===" % (source_id, len(relevant)))
    print("-" * 60)
    print("%-20s %6s %6s %s" % ("Time", "Fetch", "Dur(ms)", "Status"))
    print("-" * 60)

    for ts, info in relevant:
        fetched = info.get("fetched", 0)
        dur = info.get("duration_ms", 0)
        errs = info.get("errors", [])
        status = "ERR: %s" % errs[0][:40] if errs else "OK"
        print("%-20s %6d %6.0f  %s" % (ts, fetched, dur, status))
