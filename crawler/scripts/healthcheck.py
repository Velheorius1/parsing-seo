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
7. E-IMZO auth tokens for all platforms (ebirja, hayotbirja, xt-xarid, xarid-ebirja)
8. Mac E-IMZO daemon heartbeat (cycle_status, stale, flap detection)

Usage:
    python3 -m crawler.scripts.healthcheck               # check all
    python3 -m crawler.scripts.healthcheck --fix          # check + auto-fix
    python3 -m crawler.scripts.healthcheck --telegram     # send report to Telegram (unconditional)
    python3 -m crawler.scripts.healthcheck --alert-on-fail  # send TG only if any FAIL, with 4h dedup
    python3 -m crawler.scripts.healthcheck --json         # JSON output (no token values leaked)

Hourly cron (VPS):
    # /etc/cron.d/parsing-seo-health:
    # 0 * * * * root cd /opt/parsing-seo && /opt/parsing-seo/.venv/bin/python3 -m crawler.scripts.healthcheck --alert-on-fail

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


def is_source_file(name):
    # type: (str) -> bool
    """Настоящий исходник, а не macOS-огрызок.

    `scp` с Мака вместе с `foo.py` кладёт рядом `._foo.py` — ресурсную вилку
    AppleDouble: бинарный мусор с расширением .py. На проде их 59 штук от 16.03,
    и они делают ровно две гадости. Первая уже случилась: обход исходников в
    test_reasoning_disabled падает на них UnicodeDecodeError, и проверка флага
    reasoning:false молча не работает. Вторая — хуже и ещё не случилась: свежесть
    кода здесь меряется по mtime файлов в core/, и огрызок из нового scp окажется
    НОВЕЕ времени старта сервиса, то есть healthcheck выдаст «STALE CODE» на
    ровном месте и пошлёт перезапускать исправный бот.
    """
    return name.endswith(".py") and not name.startswith("._")


# ── Status codes ──
OK = "ok"
WARN = "warn"
FAIL = "fail"
FIXED = "fixed"
UNKNOWN = "unknown"  # cascade-suppressed dependent checks (see _format_alert_body)

# ── Alert dedup / cascade suppression config (RISK-6 mitigation) ──
# ALERT_STATE_KEY lives in crawler.auth.constants (cross-module key — see
# .conventions/gold-standards/crawler-settings-key.py).
from crawler.auth.constants import ALERT_STATE_KEY  # noqa: E402
ALERT_DEDUP_SECONDS = 4 * 3600  # same FAIL signature within 4h → skip send
# Supabase FAIL collapses the alert body; these components are treated as
# UNKNOWN (not FAIL) in the rendered body so the alert signature stays stable.
SUPABASE_DEPENDENT_COMPONENTS = (
    "freshness", "sources", "sources.low", "telegram",
    "token.", "geo.", "geo_sources", "eimzo_auth",
)

STATUS_ICONS = {
    "ok": "✅", "warn": "⚠️", "fail": "❌", "fixed": "🔧", "unknown": "❓",
}

# Mac daemon heartbeat constants — removed 2026-04-19 as Mac daemon is deprecated
# in favor of VPS /opt/eimzo/auth.py cron. See check_eimzo_auth().


class HealthCheck:
    """Run all health checks and collect results."""

    def __init__(self):
        self.results = []  # type: List[Dict[str, Any]]
        self.client = None
        self.settings = None

    def _add(self, component, status, message, details=None):
        # type: (str, str, str, Optional[Dict[str, Any]]) -> None
        entry = {
            "component": component,
            "status": status,
            "message": message,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        if details:
            entry["details"] = details
        self.results.append(entry)
        icon = STATUS_ICONS.get(status, "?")
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
        """Связь с Supabase и наличие данных.

        Связь проверялась через `count="exact"` — полный проход по всей таблице.
        На 640k строк он занимает ~7.5с и уже перебивает statement timeout:
        29.07 проверка дважды подряд выдала FAIL «Connection failed», хотя база
        отвечала мгновенно, краулер писал штатно, а три прямых запроса подряд
        прошли. Это худший сорт ложной тревоги — он растёт вместе с таблицей и
        приучает не верить красному.

        Теперь связь проверяется тем, чем она и является: одной дешёвой строкой
        (~0.25с). Размер берётся оценкой планировщика (~0.3с, расхождение с
        точным счётом 0.5% — для healthcheck достаточно), а её отказ отдельным
        WARN, а не FAIL'ом связи.
        """
        try:
            client = self._get_client()
            probe = client.table("tenders").select("id").limit(1).execute()
        except Exception as exc:
            self._add("supabase", FAIL, "Connection failed: %s" % str(exc)[:80])
            return
        if not (probe.data or []):
            self._add("supabase", WARN, "Connected but 0 tenders")
            return

        try:
            result = client.table("tenders").select("id", count="estimated").limit(0).execute()
            self._add("supabase", OK, "Connected. ~%d tenders (оценка)" % (result.count or 0))
        except Exception as exc:
            self._add("supabase", OK, "Connected (строки читаются)")
            self._add("supabase.count", WARN,
                      "счётчик строк не ответил: %s" % str(exc)[:60])

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
        from crawler.core.db import query_with_retry
        try:
            client = self._get_client()
            # Get sources with recent data (last 7 days)
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            # Paginate to bypass Supabase default limit=1000
            source_counts = {}  # type: dict
            offset = 0
            while True:
                result = query_with_retry(
                    lambda o=offset: client.table("tenders").select("source")
                    .gte("collected_at", week_ago).range(o, o + 999).execute(),
                    label="healthcheck sources page")
                if not result.data:
                    break
                for row in result.data:
                    src = row.get("source", "unknown")
                    source_counts[src] = source_counts.get(src, 0) + 1
                if len(result.data) < 1000:
                    break
                offset += 1000
                if offset > 200000:
                    break

            if not source_counts:
                self._add("sources", FAIL, "No tenders collected in last 7 days")
                return

            active = len(source_counts)
            total_records = sum(source_counts.values())
            self._add("sources", OK, "%d active sources, %d records in last 7 days" % (active, total_records))

            # Check for sources with very few records
            low_sources = [s for s, c in source_counts.items() if c < 5]
            if low_sources:
                self._add("sources.low", WARN, "%d sources with <5 records: %s" % (
                    len(low_sources), ", ".join(low_sources[:5])))
        except Exception as exc:
            # A transient statement timeout (57014) is NOT an outage — query_with_retry
            # already gave each page 3 tries. Don't page Daniyar with a false FAIL (seen
            # 2026-07-21); WARN so a sustained problem still surfaces without crying wolf.
            self._add("sources", WARN, "Source check unavailable (transient?): %s" % str(exc)[:80])

    # ── Check 4: Feedback Bot ──

    def check_feedback_bot(self):
        # type: () -> None
        """Check if feedback_bot systemd service is running (VPS only)."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "parsing-feedback-bot"],
                capture_output=True, text=True, timeout=5,
            )
            status = result.stdout.strip()
            if status != "active":
                self._add("feedback_bot", FAIL, "systemd service: %s" % status)
                return
            # Stale-process guard (2026-07-05 incident): the bot ran Apr-19 code
            # while feedback.py gained the auto-mute learning on Jul-01 — 148 ❌
            # clicks recorded, ZERO learned. A long-lived process must be newer
            # than the modules it imports.
            try:
                ts = subprocess.run(
                    ["systemctl", "show", "parsing-feedback-bot",
                     "-p", "ExecMainStartTimestamp", "--value"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                import os as _os
                from datetime import datetime as _dt
                started = _dt.strptime(" ".join(ts.split()[1:3]), "%Y-%m-%d %H:%M:%S")
                code_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
                newest = max(
                    _os.path.getmtime(_os.path.join(r, f))
                    for r, _d, fs in _os.walk(_os.path.join(code_dir, "core"))
                    for f in fs if is_source_file(f))
                newest = max(newest, _os.path.getmtime(
                    _os.path.join(code_dir, "scripts", "feedback_bot.py")))
                if newest > started.timestamp() + 60:
                    self._add("feedback_bot", FAIL,
                              "STALE CODE: service started %s but crawler code is newer — "
                              "restart parsing-feedback-bot" % ts[:20])
                else:
                    self._add("feedback_bot", OK, "active, code fresh (started %s)" % ts[:20])
            except Exception:
                self._add("feedback_bot", OK, "systemd service active (staleness unchecked)")
        except FileNotFoundError:
            self._add("feedback_bot", WARN, "systemctl not found (not on VPS?)")
        except Exception as exc:
            self._add("feedback_bot", WARN, "Could not check: %s" % str(exc)[:60])

    def check_deploy_fresh(self):
        # type: () -> None
        """Prod must actually BE the commit we think it is.

        Auto-deploy is `git pull --ff-only` every 5 min, and it aborts silently
        on a dirty working tree — a file edited or scp'd into /opt/parsing-seo
        freezes every later commit while the crawler keeps running happily on
        old code. That happened 2026-07-28 (a benchmark file copied in by hand
        held prod one commit back for ~25 minutes, found by accident, not by a
        monitor). Nothing watched for it, so nothing said anything.
        """
        import os as _os
        repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        if not _os.path.isdir(_os.path.join(repo, ".git")):
            self._add("deploy_fresh", WARN, "not a git checkout (%s)" % repo)
            return

        def _git(*args):
            return subprocess.run(("git",) + args, cwd=repo, capture_output=True,
                                  text=True, timeout=25).stdout.strip()

        try:
            subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo,
                           capture_output=True, timeout=45)
            behind = _git("rev-list", "--count", "HEAD..origin/main")
            dirty = [l for l in _git("status", "--porcelain").splitlines()
                     if l and not l.startswith("??")]
            # logs/ is written by the crons themselves — tracked churn there is
            # expected and does not block a fast-forward of other paths.
            blocking = [l for l in dirty if "logs/" not in l]
            head = _git("rev-parse", "--short", "HEAD")

            if blocking:
                self._add("deploy_fresh", FAIL,
                          "рабочее дерево грязное (%s) — auto-deploy встал, прод на %s, "
                          "позади origin на %s коммит(ов). Вернуть: git checkout -- <файл>"
                          % (", ".join(x[3:] for x in blocking[:3]), head, behind or "?"))
            elif behind and behind != "0":
                self._add("deploy_fresh", FAIL,
                          "прод на %s, позади origin/main на %s коммит(ов) — "
                          "проверь cron авто-деплоя" % (head, behind))
            else:
                self._add("deploy_fresh", OK, "%s == origin/main, дерево чистое" % head)
        except Exception as exc:
            self._add("deploy_fresh", WARN, "не смог проверить: %s" % str(exc)[:70])

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
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"),
            "/root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
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
        """FAIL if a decommissioned duplicate crawler is running, or if any running
        container ships crawler code older than prod.

        INVERTED 2026-07-17 — this check used to EXPECT 'tender-crawler' running and warn
        when it was MISSING. Exactly backwards: that container WAS the bug. It ran a full
        crawl every 2h and alerted with an image built 2026-06-07, i.e. code predating
        auto-mute, 3-tier routing, e-shop demote and the V1 verifier. Because it crawled
        more often than the cron crawler it alerted FIRST, the tender stopped being "new",
        and the fixed path never routed it — weeks-old mutes still pushed ~100%. Its code
        is baked into the image, so the */5 git auto-deploy could never fix it. Stopped
        2026-07-17. See docs/findings/2026-07-17-duplicate-stale-docker-crawler.md

        The generic staleness guard below is the real lesson: ANY container shipping frozen
        crawler code silently undoes deployed fixes while the main path's logs stay clean.
        The systemd stale-guard (check_feedback_bot) never saw this — it only covers units.
        """
        # Containers retired by an incident: their presence is a failure, not health.
        decommissioned = {"tender-crawler"}
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=10,
            )
            running = set(result.stdout.split())
        except FileNotFoundError:
            self._add("docker", WARN, "docker not found")
            return
        except Exception as exc:
            self._add("docker", WARN, "Docker check failed: %s" % str(exc)[:60])
            return

        rogue = sorted(decommissioned & running)
        if rogue:
            self._add("docker", FAIL,
                      "DECOMMISSIONED crawler is running again: %s — it alerts with frozen "
                      "image code and silently undoes mute/routing/verifier. Stop it: "
                      "docker update --restart=no %s && docker stop %s"
                      % (", ".join(rogue), rogue[0], rogue[0]))
            return

        stale = self._stale_crawler_containers(running)
        if stale:
            self._add("docker", FAIL,
                      "Container(s) ship crawler code older than prod: %s — a frozen image "
                      "bypasses git auto-deploy and re-undoes deployed fixes. Rebuild or stop."
                      % ", ".join(stale))
        else:
            self._add("docker", OK,
                      "no decommissioned/stale crawler containers (%d running)" % len(running))

    def _stale_crawler_containers(self, running):
        # type: (set) -> list
        """Running containers whose baked crawler code is >1 day older than prod's.
        Non-crawler containers are skipped silently (the stat just fails). Fail-open."""
        import os as _os
        out = []  # type: list
        try:
            code_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            prod_newest = max(
                _os.path.getmtime(_os.path.join(r, f))
                for r, _d, fs in _os.walk(_os.path.join(code_dir, "core"))
                for f in fs if is_source_file(f))
        except Exception:
            return out  # can't establish a baseline → don't guess
        for name in sorted(running):
            try:
                r = subprocess.run(
                    ["docker", "exec", name, "stat", "-c", "%Y", "/app/crawler/core/notifier.py"],
                    capture_output=True, text=True, timeout=8,
                )
                if r.returncode != 0 or not r.stdout.strip():
                    continue  # not a crawler container
                if float(r.stdout.strip()) < prod_newest - 86400:
                    out.append(name)
            except Exception:
                continue
        return out

    # ── Check 13: Tokens ──

    def check_tokens(self):
        # type: () -> None
        """Check auth token expiry for all E-IMZO platforms + Supabase.

        Iterates ``PLATFORMS`` from ``crawler.auth_eimzo`` (the canonical
        platform list) and reports per-platform status. Never logs or stores
        the token value itself — only metadata ``expires_at``, ``source``,
        ``obtained_at`` (RISK-3 mitigation).
        """
        try:
            from crawler.auth_eimzo import PLATFORMS
            from crawler.auth.session_store import session_store
        except Exception as exc:
            self._add("tokens", WARN, "Could not import PLATFORMS: %s" % str(exc)[:60])
            return

        now = datetime.now(timezone.utc)
        # cooperation logs in via the separate /opt/eimzo/coop_login.py (not in
        # PLATFORMS) — it was invisible here, so its token died 01-05.07 with
        # ZERO alerts while collection silently stopped. Check it explicitly.
        for platform_id in list(PLATFORMS.keys()) + ["cooperation"]:
            comp = "token.%s" % platform_id
            try:
                # Use session_store's internal reader to get stored metadata.
                # We need expires_at regardless of whether the token is "valid" —
                # so _read (not get_token, which returns None on expiry).
                data = session_store._read(platform_id)
                if not data:
                    self._add(comp, WARN, "No token stored for %s" % platform_id)
                    continue

                expires_at = data.get("expires_at") or ""
                source = data.get("source") or "unknown"
                obtained_at = data.get("obtained_at") or ""
                details = {
                    "expires_at": expires_at,
                    "source": source,
                    "obtained_at": obtained_at,
                }  # NOTE: never include "token" key here (RISK-3)

                if not expires_at:
                    self._add(comp, WARN,
                              "No expiry stored for %s" % platform_id, details=details)
                    continue

                try:
                    exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    self._add(comp, WARN,
                              "Could not parse expiry for %s: %s" % (platform_id, expires_at[:30]),
                              details=details)
                    continue

                remaining_h = (exp_dt - now).total_seconds() / 3600
                if remaining_h > 1:
                    self._add(comp, OK,
                              "%s token valid (%.1fh remaining)" % (platform_id, remaining_h),
                              details=details)
                elif remaining_h > 0:
                    self._add(comp, WARN,
                              "%s expires in %.0f min" % (platform_id, remaining_h * 60),
                              details=details)
                else:
                    self._add(comp, FAIL,
                              "%s EXPIRED %.1fh ago" % (platform_id, abs(remaining_h)),
                              details=details)
            except Exception as exc:
                self._add(comp, WARN,
                          "Token check failed for %s: %s" % (platform_id, str(exc)[:60]))

        # Supabase API key check.
        # New 'sb_secret_*' keys (2026+) never expire — only manual Reset in Dashboard.
        # Old-style JWT (eyJ...) carries 'exp' claim → decode and check.
        # Live connection is already verified by check_supabase().
        try:
            import json, base64
            key = self.settings.supabase_service_role_key if self.settings else ""
            if not key:
                self._add("token.supabase", WARN, "SUPABASE_SERVICE_ROLE_KEY not set")
            elif key.startswith("sb_secret_") or key.startswith("sb_publishable_"):
                self._add("token.supabase", OK, "Supabase API key (sb_secret format, no expiry)")
            elif key.startswith("eyJ") and key.count(".") == 2:
                payload = key.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                exp_ts = claims.get("exp")
                if not exp_ts:
                    self._add("token.supabase", OK, "Supabase JWT has no exp claim")
                else:
                    exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
                    days_left = (exp_dt - now).days
                    if days_left > 7:
                        self._add("token.supabase", OK, "Supabase JWT valid (%d days left)" % days_left)
                    elif days_left > 0:
                        self._add("token.supabase", WARN, "Supabase JWT expires in %d days" % days_left)
                    else:
                        self._add("token.supabase", FAIL, "Supabase JWT EXPIRED!")
            else:
                self._add("token.supabase", WARN, "Unknown Supabase key format")
        except Exception as exc:
            self._add("token.supabase", WARN, "Supabase key check failed: %s" % str(exc)[:60])

    # ── Check 13b: Per-source freshness SLO ──

    def check_dead_sources(self):
        # type: () -> None
        """Detect sources that produced 0 records in last 7 days despite being enabled.

        Fired as WARN per source (not FAIL — we already alert globally via freshness).
        Whitelist for legitimately low-volume sources lives in DEAD_SOURCES_WHITELIST.
        """
        # Sources expected to be silent for stretches (legacy/low-volume) — don't alert.
        # Sources that are legitimately low-volume — alerting on them is noise.
        # International (low UZ relevance), banks (slow updates), niche TG channels.
        # Re-evaluate this list quarterly.
        DEAD_SOURCES_WHITELIST = {
            # International orgs — UZ tenders are rare
            'UNDP Procurement',
            'UN Global Marketplace',
            'World Bank',
            'Asian Development Bank',
            'Islamic Development Bank (IsDB)',
            'EBRD',
            'GIZ',
            'JICA',
            'KOICA',
            'USAID',
            'EU TED',
            # Banks — quarterly tender publishers
            'InFinBank',
            'Orient Finance Bank',
            'Sanoat Qurilish Bank',
            'Asia Alliance Bank',
            'Hamkorbank',
            # Low-volume TG mirrors
            'TG: PR UZB (запросы клиентов)',
            'TG: UZEX Xarid Official',
            'TG: Закупки Prom.uz',
            'TG: Фонд предпринимательства',
            'TG: Узбекистон Темир Йуллари',
            'TG: Хамкорбанк',
            'TG: Мин ИТ',
            # Cooperation legacy (replaced by cooperation-plans-filtered)
            'Cooperation.uz Брошюры/Буклеты',
            'Cooperation.uz Аукционы',
            'Cooperation.uz Закупочные планы',
            'Cooperation.uz Э-магазин лоты',
            'Cooperation.uz Bosma (узб.)',
            # Other quiet mirrors
            'Узбекистон Темир Йуллари',
            'Минстрой (tender.mc.uz)',
            'E-Birja активные аукционы (xarid)',
        }
        try:
            client = self._get_client()
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

            # Load enabled sources from yaml
            import yaml as _yaml
            yaml_path = os.path.join(os.path.dirname(__file__), '../config/sources.yaml')
            with open(os.path.abspath(yaml_path), 'r', encoding='utf-8') as f:
                cfg = _yaml.safe_load(f)
            enabled_names = [s.get('name') for s in cfg.get('sources', []) if s.get('enabled') and s.get('name')]

            # Get source→count map for last 7d via pagination (Supabase default limit=1000)
            counts = {}
            offset = 0
            while True:
                r = client.table('tenders').select('source').gte('collected_at', week_ago).range(offset, offset + 999).execute()
                if not r.data:
                    break
                for row in r.data:
                    src = row.get('source')
                    if src:
                        counts[src] = counts.get(src, 0) + 1
                if len(r.data) < 1000:
                    break
                offset += 1000
                if offset > 200000:  # safety cap
                    break

            dead = [name for name in enabled_names
                    if name not in DEAD_SOURCES_WHITELIST and counts.get(name, 0) == 0]
            if dead:
                # Show up to 8 dead sources in message; rest in details
                head = ', '.join(dead[:8])
                more = ('' if len(dead) <= 8 else ' (+%d more)' % (len(dead) - 8))
                self._add('sources.dead_7d', WARN,
                          '%d enabled sources with 0 records in 7d: %s%s' % (len(dead), head, more),
                          details={'dead_sources': dead})
            else:
                self._add('sources.dead_7d', OK, 'All enabled sources have data in last 7d')
        except Exception as exc:
            self._add('sources.dead_7d', WARN, 'Dead-source check failed: %s' % str(exc)[:80])

    # ── Check 14: VPS E-IMZO Auth Cron ──

    def check_eimzo_auth(self):
        # type: () -> None
        """Check that VPS /opt/eimzo/auth.py cron is refreshing ebirja JWTs.

        Reads ``auth_token:ebirja`` from crawler_settings — written by
        ``/opt/eimzo/auth.py`` (cron every 4h) with ``source=auto-vps-eimzo``.
        Mac daemon is deprecated as of 2026-04-19.

        Reports:
        - FAIL if token missing
        - FAIL if obtained_at >8h old (cron not running)
        - WARN if obtained_at >5h old (close to expiry)
        - WARN if source != auto-vps-eimzo (legacy mac source still writing)
        - OK otherwise
        """
        try:
            from crawler.auth.session_store import session_store
        except Exception as exc:
            self._add("eimzo_auth", WARN,
                      "Could not import session_store: %s" % str(exc)[:60])
            return

        client = session_store._get_client()
        if client is None:
            self._add("eimzo_auth", WARN, "Supabase unreachable")
            return

        try:
            resp = client.table("crawler_settings").select("value").eq(
                "key", "auth_token:ebirja",
            ).execute()
        except Exception as exc:
            self._add("eimzo_auth", WARN, "Read failed: %s" % str(exc)[:60])
            return

        if not resp.data:
            self._add("eimzo_auth", FAIL,
                      "No ebirja token — VPS auth.py cron not running")
            return

        try:
            payload = json.loads(resp.data[0]["value"])
        except (ValueError, TypeError, KeyError):
            self._add("eimzo_auth", WARN, "Token value malformed JSON")
            return

        source = payload.get("source") or "unknown"
        obtained_at = payload.get("obtained_at") or ""
        try:
            obt_dt = datetime.fromisoformat(obtained_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            self._add("eimzo_auth", WARN,
                      "Token obtained_at unparseable: %s" % obtained_at[:40])
            return

        age_h = (datetime.now(timezone.utc) - obt_dt).total_seconds() / 3600
        details = {"source": source, "obtained_at": obtained_at, "age_h": round(age_h, 2)}

        if age_h > 8:
            self._add("eimzo_auth", FAIL,
                      "Ebirja token %.1fh old — VPS cron stuck" % age_h,
                      details=details)
        elif age_h > 5:
            self._add("eimzo_auth", WARN,
                      "Ebirja token %.1fh old — past 5h refresh interval" % age_h,
                      details=details)
        elif source != "auto-vps-eimzo":
            self._add("eimzo_auth", WARN,
                      "Ebirja token source=%s (expected auto-vps-eimzo)" % source,
                      details=details)
        else:
            self._add("eimzo_auth", OK,
                      "Ebirja JWT refreshed %.1fh ago via VPS cron" % age_h,
                      details=details)

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
                        ["systemctl", "restart", "parsing-feedback-bot"],
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
            icon = STATUS_ICONS.get(r["status"], "?")
            lines.append("%s %-20s %s" % (icon, r["component"], r["message"]))

        return "\n".join(lines)

    # ── Alert body formatting + dedup (RISK-6 mitigation) ──

    @staticmethod
    def _is_supabase_dependent(component):
        # type: (str) -> bool
        """Return True if ``component`` depends on Supabase availability."""
        for prefix in SUPABASE_DEPENDENT_COMPONENTS:
            if component == prefix or component.startswith(prefix):
                return True
        return False

    def _format_alert_body(self, fails, suppress_cascade=True):
        # type: (List[Dict[str, Any]], bool) -> str
        """Build the Telegram alert body.

        If ``suppress_cascade`` is True and any FAIL component is ``supabase``,
        render ONLY the supabase line + a footer noting that downstream checks
        are suppressed. Dependent checks are rewritten as ``unknown`` in the
        rendered body so the alert signature stays stable across runs.
        """
        supabase_fail = any(f["component"] == "supabase" for f in fails)

        if suppress_cascade and supabase_fail:
            root = next(f for f in fails if f["component"] == "supabase")
            body = [
                "❌ PARSING-SEO HEALTHCHECK FAIL",
                "",
                "❌ %-20s %s" % ("supabase", root["message"]),
                "",
                "(downstream checks suppressed because Supabase is the root cause)",
            ]
            return "\n".join(body)

        # Normal render: all FAILs.
        lines = ["❌ PARSING-SEO HEALTHCHECK FAIL", ""]
        for f in fails:
            lines.append("❌ %-20s %s" % (f["component"], f["message"]))
        return "\n".join(lines)

    def _compute_alert_signature(self, suppress_cascade=True):
        # type: (bool) -> str
        """Sorted-comma-joined FAIL component names (after cascade suppression)."""
        fails = [r for r in self.results if r["status"] == FAIL]
        if suppress_cascade and any(f["component"] == "supabase" for f in fails):
            return "supabase"
        return ",".join(sorted(f["component"] for f in fails))

    def handle_alert_on_fail(self):
        # type: () -> Optional[str]
        """Send TG alert only if any FAIL, with 4h dedup + cascade suppression.

        Behavior:
        - Compute alert signature (cascade-suppressed).
        - If signature is empty (no FAILs) AND prior state is non-empty → send
          one RECOVERY message and clear state.
        - If signature matches prior state AND last send was <4h ago → skip.
        - Otherwise → send alert, update state.

        Returns the action taken: ``"sent"``, ``"recovery"``, ``"suppressed"``,
        or ``"no_op"``. ``None`` if Telegram is not configured.
        """
        try:
            from crawler.auth.session_store import session_store
        except Exception as exc:
            logger.warning("Alert dedup: import failed: %s", str(exc)[:60])
            return None

        settings = self.settings
        if not settings or not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
            logger.info("Telegram not configured — skipping --alert-on-fail")
            return None

        fails = [r for r in self.results if r["status"] == FAIL]
        signature = self._compute_alert_signature(suppress_cascade=True)
        now = datetime.now(timezone.utc)

        prior = session_store.get_setting(ALERT_STATE_KEY) or {}
        prior_sig = prior.get("signature") or ""
        prior_at_str = prior.get("alerted_at") or ""

        # Case 1: all clear and we had an active alert → RECOVERY.
        if not signature:
            if prior_sig:
                self._send_alert_body("✅ PARSING-SEO RECOVERY\n\nAll checks passing.")
                session_store.set_setting(ALERT_STATE_KEY, {})
                logger.info("Sent RECOVERY message, cleared %s", ALERT_STATE_KEY)
                return "recovery"
            return "no_op"

        # Case 2: same signature, within dedup window → suppress.
        # Guard against clock skew (NTP correction, VM restore, DST anomaly):
        # if prior_at is in the future, delta is negative — reset state rather
        # than silence legitimate FAILs until the clock catches up.
        if signature == prior_sig and prior_at_str:
            try:
                prior_at = datetime.fromisoformat(prior_at_str.replace("Z", "+00:00"))
                delta = (now - prior_at).total_seconds()
                if 0 <= delta < ALERT_DEDUP_SECONDS:
                    logger.info(
                        "Suppressed duplicate alert (sig=%s, last sent %.1fh ago)",
                        signature, delta / 3600,
                    )
                    return "suppressed"
                if delta < 0:
                    logger.warning(
                        "Clock skew detected (prior alerted_at in future by %.1fh) — resetting dedup state",
                        -delta / 3600,
                    )
                    # Fall through to Case 3 (send + rewrite state).
            except (ValueError, TypeError):
                pass

        # Case 3: send alert, update state.
        body = self._format_alert_body(fails, suppress_cascade=True)
        self._send_alert_body(body)
        session_store.set_setting(ALERT_STATE_KEY, {
            "signature": signature,
            "alerted_at": now.isoformat(),
        })
        logger.info("Sent alert (sig=%s)", signature)
        return "sent"

    def _send_alert_body(self, body):
        # type: (str) -> None
        """POST ``body`` to the configured Telegram alert chat."""
        try:
            import httpx
            settings = self.settings
            httpx.post(
                "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                json={
                    "chat_id": settings.telegram_alert_chat_id,
                    "text": body,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Failed to send alert: %s", str(exc)[:80])

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
    parser.add_argument("--telegram", action="store_true",
                        help="Send full report to Telegram unconditionally")
    parser.add_argument("--alert-on-fail", action="store_true",
                        help="Send TG alert only if any FAIL, with 4h dedup + cascade suppression")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    hc = HealthCheck()

    # Run all checks
    hc.check_supabase()
    hc.check_freshness()
    hc.check_sources()
    hc.check_dead_sources()
    hc.check_feedback_bot()
    hc.check_cron()
    hc.check_deploy_fresh()
    hc.check_telegram()
    hc.check_api_endpoints()
    hc.check_playwright()
    hc.check_disk()
    hc.check_zombie_processes()
    hc.check_geo_sources()
    hc.check_docker()
    hc.check_tokens()
    hc.check_eimzo_auth()

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

    if args.alert_on_fail:
        hc.handle_alert_on_fail()

    # Exit code
    has_fail = any(r["status"] == FAIL for r in hc.results if r["status"] != FIXED)
    sys.exit(1 if has_fail else 0)


if __name__ == "__main__":
    main()
