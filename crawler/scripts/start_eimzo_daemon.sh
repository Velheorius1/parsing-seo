#!/bin/bash
# DEPRECATED 2026-04-19 — VPS auth.py cron replaced this Mac flow.
# See /opt/eimzo/auth.py (cron: 0 */4 * * * /opt/eimzo/run_auth.sh).
# Kept only for git history. Do not start; will refuse without the flag.
if [ "${1:-}" != "--force-deprecated" ]; then
    echo "DEPRECATED: token refresh now runs on VPS via /opt/eimzo/auth.py cron." >&2
    echo "Pass --force-deprecated to run anyway (debugging only)." >&2
    exit 1
fi
shift  # consume --force-deprecated

# Supervisor wrapper for mac_eimzo_daemon.py.
# - Pre-checks pgrep (belt-and-suspenders alongside the daemon's own flock)
# - Wraps under caffeinate -dis so Mac never sleeps mid-refresh
# - Size-based log rotation (>10MB → daemon.log.1), checked before every spawn
# - Auto-restart with exponential backoff capped at 300s
# - Backoff resets to 10s after a daemon run that stayed alive >600s
#
# Usage:
#     bash crawler/scripts/start_eimzo_daemon.sh    # run in tmux
#     E_IMZO_KEY_TIN=123456789 bash crawler/scripts/start_eimzo_daemon.sh
#
# Required env vars: E_IMZO_KEY_TIN. Optional: E_IMZO_PLATFORMS,
# EIMZO_DAEMON_REFRESH_SECONDS. See README_eimzo_daemon.md.
#
# PIN: E-IMZO v6.3.5 collects the PFX PIN via its own GUI dialog the first time
# ``pfx.load_key`` is called. The daemon caches the resulting keyId in Supabase,
# so subsequent restarts are silent unless E-IMZO itself restarts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
LOG_DIR="${HOME}/.eimzo_daemon"
LOG="${LOG_DIR}/daemon.log"
MAX_LOG_BYTES=10485760
STABLE_SECONDS=600
mkdir -p "$LOG_DIR"

rotate_log_if_large() {
    if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt "$MAX_LOG_BYTES" ]; then
        mv "$LOG" "${LOG}.1"
    fi
}

# Pre-check — daemon also holds an fcntl flock, this is an early exit for clarity
if pgrep -f "mac_eimzo_daemon.py" >/dev/null; then
    echo "[start_eimzo_daemon] Daemon already running (PIDs: $(pgrep -f mac_eimzo_daemon.py | tr '\n' ' '))"
    exit 0
fi

BACKOFF=10
while true; do
    # M3: rotate before every invocation, not just once at startup
    rotate_log_if_large
    echo "[start_eimzo_daemon] $(date -Iseconds) starting daemon" >> "$LOG"
    start_ts=$(date +%s)
    if caffeinate -dis python3 "${PROJECT_ROOT}/crawler/scripts/mac_eimzo_daemon.py" >> "$LOG" 2>&1; then
        echo "[start_eimzo_daemon] $(date -Iseconds) daemon exited cleanly" >> "$LOG"
        exit 0
    fi
    end_ts=$(date +%s)
    ran_for=$(( end_ts - start_ts ))
    # M4: stable run before crash → reset backoff so we respond fast to a single flake
    if [ "$ran_for" -gt "$STABLE_SECONDS" ]; then
        BACKOFF=10
    fi
    echo "[start_eimzo_daemon] $(date -Iseconds) daemon crashed after ${ran_for}s, sleeping ${BACKOFF}s" >> "$LOG"
    sleep "$BACKOFF"
    BACKOFF=$(( BACKOFF * 3 ))
    if [ "$BACKOFF" -gt 300 ]; then BACKOFF=300; fi
done
