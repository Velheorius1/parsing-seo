#!/usr/bin/env python3
"""Healthcheck & auto-repair for parsing-seo crawler system.

Checks all components, reports status, and auto-fixes common issues.

Components checked:
1. Supabase connection + data freshness
2. Crawler sources (enabled vs working)
3. VPS cron jobs
4. Mac cron jobs (cooperation, UZEX)
5. Feedback bot (systemd)
6. Telegram alerts delivery

Usage:
    python3 -m crawler.scripts.healthcheck               # check all
    python3 -m crawler.scripts.healthcheck --fix          # check + auto-fix
    python3 -m crawler.scripts.healthcheck --telegram     # send report to Telegram
    python3 -m crawler.scripts.healthcheck --json         # JSON output

Requires: supabase, httpx, python-dotenv
"""

import argparse
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# ── Status codes ──
OK = "ok"
WARN = "warn"
FAIL = "fail"
FIXED = "fixed"


class HealthCheck:
    """Run all health checks and collect results."""

    def __init__(self):
        self.results = []  # type: List[Dict[str, Any]]
        self.client = None
        self.settings = None

    def _add(self, component, status, message, details=None):
        # type: (str, str, str, Optional[str]) -> None
        entry = {
            "component": component,
            "status": status,
            "message": message,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        if details:
            entry["details"] = details
        self.results.append(entry)
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌", "fixed": "🔧"}.get(status, "?")
        logger.info("%s [%s] %s: %s", icon, status.upper(), component, message)

    def _get_client(self):
        if self.client is None:
            from crawler.config.settings import settings
            from supabase import create_client
            self.settings = settings
            self.client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        return self.client

    # ── Check 1: Supabase Connection ──

    def check_supabase(self):
        # type: () -> None
        """Check Supabase connection and basic data."""
        try:
            client = self._get_client()
            result = client.table("tenders").select("id", count="exact").limit(0).execute()
            total = result.count or 0
            if total > 0:
                self._add("supabase", OK, "Connected. %d tenders total" % total)
            else:
                self._add("supabase", WARN, "Connected but 0 tenders")
        except Exception as exc:
            self._add("supabase", FAIL, "Connection failed: %s" % str(exc)[:80])

    # ── Check 2: Data Freshness ──

    def check_freshness(self):
        # type: () -> None
        """Check if data is fresh (crawled recently)."""
        try:
            client = self._get_client()
            result = client.table("crawl_runs").select(
                "started_at, total_fetched, total_new"
            ).order("started_at", desc=True).limit(5).execute()

            if not result.data:
                self._add("freshness", WARN, "No crawl runs found")
                return

            latest = result.data[0]
            started = latest.get("started_at", "")
            if started:
                # Parse ISO datetime
                try:
                    dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    if age_hours < 4:
                        self._add("freshness", OK, "Last crawl %.1fh ago (%d fetched, %d new)" % (
                            age_hours, latest.get("total_fetched", 0), latest.get("total_new", 0)))
                    elif age_hours < 8:
                        self._add("freshness", WARN, "Last crawl %.1fh ago (may be stale)" % age_hours)
                    else:
                        self._add("freshness", FAIL, "Last crawl %.1fh ago (STALE!)" % age_hours)
                except Exception:
                    self._add("freshness", WARN, "Could not parse crawl time: %s" % started[:30])
            else:
                self._add("freshness", WARN, "No started_at in crawl_run")
        except Exception as exc:
            self._add("freshness", FAIL, "Could not check freshness: %s" % str(exc)[:80])

    # ── Check 3: Source Health ──

    def check_sources(self):
        # type: () -> None
        """Check which sources have data and which are dead."""
        try:
            client = self._get_client()
            # Get sources with recent data (last 7 days)
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            result = client.table("tenders").select(
                "source"
            ).gte("collected_at", week_ago).limit(50000).execute()

            if not result.data:
                self._add("sources", FAIL, "No tenders collected in last 7 days")
                return

            source_counts = {}  # type: dict
            for row in result.data:
                src = row.get("source", "unknown")
                source_counts[src] = source_counts.get(src, 0) + 1

            active = len(source_counts)
            total_records = sum(source_counts.values())
            self._add("sources", OK, "%d active sources, %d records in last 7 days" % (active, total_records))

            # Check for sources with very few records
            low_sources = [s for s, c in source_counts.items() if c < 5]
            if low_sources:
                self._add("sources.low", WARN, "%d sources with <5 records: %s" % (
                    len(low_sources), ", ".join(low_sources[:5])))
        except Exception as exc:
            self._add("sources", FAIL, "Source check failed: %s" % str(exc)[:80])

    # ── Check 4: Feedback Bot ──

    def check_feedback_bot(self):
        # type: () -> None
        """Check if feedback_bot systemd service is running (VPS only)."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "feedback-bot"],
                capture_output=True, text=True, timeout=5,
            )
            status = result.stdout.strip()
            if status == "active":
                self._add("feedback_bot", OK, "systemd service active")
            else:
                self._add("feedback_bot", FAIL, "systemd service: %s" % status)
        except FileNotFoundError:
            self._add("feedback_bot", WARN, "systemctl not found (not on VPS?)")
        except Exception as exc:
            self._add("feedback_bot", WARN, "Could not check: %s" % str(exc)[:60])

    # ── Check 5: Cron Jobs ──

    def check_cron(self):
        # type: () -> None
        """Check cron jobs are configured."""
        try:
            result = subprocess.run(
                ["crontab", "-l"],
                capture_output=True, text=True, timeout=5,
            )
            cron = result.stdout

            # VPS-only cron jobs (UZEX and cooperation run on Mac)
            is_vps = os.path.exists("/opt/parsing-seo")
            expected = {
                "crawler": "run_crawl.sh",
                "contracts": "fetch_ebirja_contracts",
                "refine_patterns": "refine_patterns",
            }
            if not is_vps:
                # Mac cron jobs
                expected = {
                    "cooperation": "cooperation",
                    "uzex": "fetch_uzex_auctions",
                }

            found = []
            missing = []
            for name, pattern in expected.items():
                if pattern in cron:
                    found.append(name)
                else:
                    missing.append(name)

            if missing:
                self._add("cron", WARN, "Missing: %s. Found: %s" % (
                    ", ".join(missing), ", ".join(found)))
            else:
                self._add("cron", OK, "All %d cron jobs found" % len(found))
        except Exception as exc:
            self._add("cron", FAIL, "Cron check failed: %s" % str(exc)[:60])

    # ── Check 6: Telegram Alert Delivery ──

    def check_telegram(self):
        # type: () -> None
        """Check if Telegram alerts are being delivered."""
        try:
            client = self._get_client()
            # Check recent alert_feedback entries (means alerts are being sent)
            result = client.table("tenders").select(
                "alert_seq", count="exact"
            ).not_.is_("alert_seq", "null").limit(0).execute()
            total_alerts = result.count or 0

            if total_alerts > 0:
                self._add("telegram", OK, "%d alerts sent (with seq numbers)" % total_alerts)
            else:
                self._add("telegram", WARN, "No alerts with seq numbers found")
        except Exception as exc:
            self._add("telegram", WARN, "Telegram check failed: %s" % str(exc)[:60])

    # ── Check 7: API Endpoints ──

    def check_api_endpoints(self):
        # type: () -> None
        """Check if key API endpoints are reachable."""
        import httpx

        endpoints = [
            ("ebirja-ext", "https://api.ebirja.uz/fond-api/api/external/contract/all?page=0&size=1"),
            ("ebirja-eshop", "https://xarid-api.ebirja.uz/shop/product/announce-list?currentPage=0&perPage=1&platform_display=e-shop"),
            ("ebirja-auctions", "https://xarid-api.ebirja.uz/auction/auction/active?page=0&size=1"),
        ]

        for name, url in endpoints:
            try:
                r = httpx.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; TenderMonitor/1.0)"
                })
                if r.status_code == 200:
                    self._add("api.%s" % name, OK, "HTTP %d" % r.status_code)
                else:
                    self._add("api.%s" % name, WARN, "HTTP %d" % r.status_code)
            except Exception as exc:
                self._add("api.%s" % name, FAIL, "Unreachable: %s" % str(exc)[:60])

    # ── Check 8: Playwright ──

    def check_playwright(self):
        # type: () -> None
        """Check if Playwright Chromium binary is installed."""
        patterns = [
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
            "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
        ]
        found = False
        for pattern in patterns:
            if glob.glob(pattern):
                found = True
                break
        if found:
            self._add("playwright", OK, "Chromium binary found")
        else:
            self._add("playwright", FAIL, "Chromium binary NOT found")

    # ── Check 9: Disk Space ──

    def check_disk(self):
        # type: () -> None
        """Check disk space."""
        usage = shutil.disk_usage("/")
        pct = usage.used / usage.total * 100
        free_gb = usage.free / (1024 ** 3)
        if pct < 80:
            self._add("disk", OK, "%.1f%% used (%.1f GB free)" % (pct, free_gb))
        elif pct < 90:
            self._add("disk", WARN, "%.1f%% used (%.1f GB free)" % (pct, free_gb))
        else:
            self._add("disk", FAIL, "%.1f%% used (%.1f GB free) — CRITICAL" % (pct, free_gb))

    # ── Check 10: Zombie Processes ──

    def check_zombie_processes(self):
        # type: () -> None
        """Check for zombie Chromium/Playwright processes."""
        try:
            result = subprocess.run(
                ["pgrep", "-af", "chromium"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                self._add("zombies", WARN, "%d chromium processes running" % len(lines))
            else:
                self._add("zombies", OK, "No zombie chromium processes")
        except Exception:
            self._add("zombies", OK, "No chromium processes (pgrep not available)")

    # ── Check 11: Geo Sources ──

    def check_geo_sources(self):
        # type: () -> None
        """Check if geo-restricted sources (cooperation, UZEX) have fresh data."""
        try:
            client = self._get_client()
            threshold = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()

            for source_pattern, name in [("Cooperation%", "Cooperation.uz"), ("UZEX%", "UZEX")]:
                result = client.table("tenders").select("collected_at").like(
                    "source", source_pattern
                ).gte("collected_at", threshold).limit(1).execute()

                if result.data:
                    self._add("geo.%s" % name.lower().replace(".", ""), OK,
                              "%s has fresh data (<12h)" % name)
                else:
                    self._add("geo.%s" % name.lower().replace(".", ""), WARN,
                              "%s data >12h stale — check crawler" % name)
        except Exception as exc:
            self._add("geo_sources", WARN, "Could not check: %s" % str(exc)[:60])

    # ── Check 12: Docker ──

    def check_docker(self):
        # type: () -> None
        """Check if key Docker containers are running."""
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=10,
            )
            running = set(result.stdout.strip().split('\n')) if result.stdout.strip() else set()
            expected = {"tender-crawler"}
            missing = expected - running
            if not missing:
                self._add("docker", OK, "All containers running: %s" % ", ".join(sorted(expected)))
            else:
                self._add("docker", WARN, "Missing containers: %s" % ", ".join(sorted(missing)))
        except FileNotFoundError:
            self._add("docker", WARN, "docker not found")
        except Exception as exc:
            self._add("docker", WARN, "Docker check failed: %s" % str(exc)[:60])

    # ── Check 13: Tokens ──

    def check_tokens(self):
        # type: () -> None
        """Check auth token expiry."""
        try:
            client = self._get_client()
            # Check E-IMZO token in session store
            result = client.table("crawler_settings").select("value").eq(
                "key", "auth_token:ebirja"
            ).limit(1).execute()

            if result.data:
                token_data = json.loads(result.data[0].get("value", "{}"))
                expires = token_data.get("expires_at", "")
                if expires:
                    try:
                        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                        remaining = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                        if remaining > 1:
                            self._add("token.ebirja", OK, "E-IMZO valid (%.1fh remaining)" % remaining)
                        elif remaining > 0:
                            self._add("token.ebirja", WARN, "E-IMZO expires in %.0f min!" % (remaining * 60))
                        else:
                            self._add("token.ebirja", FAIL, "E-IMZO EXPIRED %.1fh ago" % abs(remaining))
                    except Exception:
                        self._add("token.ebirja", WARN, "Could not parse expiry: %s" % expires[:30])
                else:
                    self._add("token.ebirja", WARN, "No expiry in E-IMZO token data")
            else:
                self._add("token.ebirja", WARN, "No E-IMZO token stored (ebirja auth disabled)")

            # Supabase access token expiry (hardcoded)
            supabase_expiry = datetime(2026, 4, 27, tzinfo=timezone.utc)
            days_left = (supabase_expiry - datetime.now(timezone.utc)).days
            if days_left > 7:
                self._add("token.supabase", OK, "Supabase token valid (%d days left)" % days_left)
            elif days_left > 0:
                self._add("token.supabase", WARN, "Supabase token expires in %d days!" % days_left)
            else:
                self._add("token.supabase", FAIL, "Supabase token EXPIRED!")
        except Exception as exc:
            self._add("tokens", WARN, "Token check failed: %s" % str(exc)[:60])

    # ── Auto-fix ──

    def auto_fix(self):
        # type: () -> None
        """Attempt to fix common issues."""
        for result in self.results:
            if result["status"] != FAIL:
                continue

            comp = result["component"]

            # Fix: feedback_bot not running
            if comp == "feedback_bot":
                try:
                    subprocess.run(
                        ["systemctl", "restart", "feedback-bot"],
                        capture_output=True, timeout=10,
                    )
                    self._add("feedback_bot", FIXED, "Restarted feedback-bot service")
                except Exception:
                    pass

            # Fix: stale data — trigger a crawl
            if comp == "freshness":
                try:
                    subprocess.Popen(
                        ["/opt/parsing-seo/scripts/run_crawl.sh", "--no-telegram"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self._add("freshness", FIXED, "Triggered new crawl")
                except Exception:
                    pass

            # Fix: Playwright Chromium not installed
            if comp == "playwright":
                try:
                    subprocess.run(
                        [sys.executable, "-m", "playwright", "install", "chromium"],
                        capture_output=True, timeout=120,
                    )
                    self._add("playwright", FIXED, "Installed Playwright Chromium")
                except Exception:
                    pass

            # Fix: Disk space critical — delete old JSONL files
            if comp == "disk":
                try:
                    cutoff = datetime.now().timestamp() - (30 * 86400)
                    deleted = 0
                    for dirpath, _dirnames, filenames in os.walk("/opt/parsing-seo"):
                        for fn in filenames:
                            if fn.endswith(".jsonl"):
                                fpath = os.path.join(dirpath, fn)
                                if os.path.getmtime(fpath) < cutoff:
                                    os.remove(fpath)
                                    deleted += 1
                    if deleted > 0:
                        self._add("disk", FIXED, "Deleted %d JSONL files >30 days old" % deleted)
                except Exception:
                    pass

        # Fix zombies (WARN, not FAIL — separate loop)
        for result in self.results:
            if result["status"] != WARN:
                continue
            comp = result["component"]

            if comp == "zombies":
                try:
                    subprocess.run(
                        ["pkill", "-f", "chromium"],
                        capture_output=True, timeout=5,
                    )
                    self._add("zombies", FIXED, "Killed zombie chromium processes")
                except Exception:
                    pass

    # ── Report ──

    def summary(self):
        # type: () -> str
        """Generate human-readable summary."""
        lines = ["=== PARSING-SEO HEALTHCHECK ===", ""]

        ok_count = sum(1 for r in self.results if r["status"] == OK)
        warn_count = sum(1 for r in self.results if r["status"] == WARN)
        fail_count = sum(1 for r in self.results if r["status"] == FAIL)
        fixed_count = sum(1 for r in self.results if r["status"] == FIXED)

        lines.append("Summary: %d OK, %d WARN, %d FAIL, %d FIXED" % (
            ok_count, warn_count, fail_count, fixed_count))
        lines.append("")

        for r in self.results:
            icon = {"ok": "✅", "warn": "⚠️", "fail": "❌", "fixed": "🔧"}.get(r["status"], "?")
            lines.append("%s %-20s %s" % (icon, r["component"], r["message"]))

        return "\n".join(lines)

    def send_telegram(self):
        # type: () -> None
        """Send summary to Telegram."""
        try:
            import httpx
            settings = self.settings
            if not settings or not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
                logger.warning("Telegram not configured, skipping")
                return

            text = self.summary()
            httpx.post(
                "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                json={
                    "chat_id": settings.telegram_alert_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            logger.info("Telegram report sent")
        except Exception as exc:
            logger.warning("Failed to send Telegram: %s", str(exc)[:80])


def main():
    parser = argparse.ArgumentParser(description="Parsing-seo healthcheck")
    parser.add_argument("--fix", action="store_true", help="Auto-fix common issues")
    parser.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    hc = HealthCheck()

    # Run all checks
    hc.check_supabase()
    hc.check_freshness()
    hc.check_sources()
    hc.check_feedback_bot()
    hc.check_cron()
    hc.check_telegram()
    hc.check_api_endpoints()
    hc.check_playwright()
    hc.check_disk()
    hc.check_zombie_processes()
    hc.check_geo_sources()
    hc.check_docker()
    hc.check_tokens()

    # Auto-fix if requested
    if args.fix:
        hc.auto_fix()

    # Output
    if args.json:
        print(json.dumps(hc.results, indent=2, ensure_ascii=False))
    else:
        print(hc.summary())

    if args.telegram:
        hc.send_telegram()

    # Exit code
    has_fail = any(r["status"] == FAIL for r in hc.results if r["status"] != FIXED)
    sys.exit(1 if has_fail else 0)


if __name__ == "__main__":
    main()
