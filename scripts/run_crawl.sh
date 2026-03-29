#!/usr/bin/env bash
# Cron entrypoint for tender crawler.
# Usage:
#   ./scripts/run_crawl.sh              # all sources
#   ./scripts/run_crawl.sh --no-telegram # skip Telegram (faster)
#   ./scripts/run_crawl.sh --only-telegram # only Telegram channels

set -euo pipefail

DIR="/opt/parsing-seo"
VENV="$DIR/.venv/bin/python"
LOG="/var/log/parsing-seo.log"
MAX_LOG_SIZE=5242880  # 5MB
LOCK_FILE="/tmp/parsing-seo-crawl.lock"

cd "$DIR"

# Overlap guard: skip if another crawl is running
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') SKIPPED: another crawl already running" >> "$LOG"
    exit 0
fi

# Rotate log if > 5MB
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || stat -f%z "$LOG" 2>/dev/null)" -gt "$MAX_LOG_SIZE" ]; then
    mv "$LOG" "${LOG}.old"
fi

# Parse arguments
EXTRA_ARGS=""
if [ "${1:-}" = "--no-telegram" ]; then
    EXTRA_ARGS="--sources $($VENV -c "
import yaml, sys
with open('$DIR/crawler/config/sources.yaml') as f:
    cfg = yaml.safe_load(f)
ids = [s['id'] for s in cfg['sources'] if s.get('enabled', True) and s.get('adapter') != 'telegram']
print(' '.join(ids))
")"
elif [ "${1:-}" = "--only-telegram" ]; then
    EXTRA_ARGS="--sources $($VENV -c "
import yaml, sys
with open('$DIR/crawler/config/sources.yaml') as f:
    cfg = yaml.safe_load(f)
ids = [s['id'] for s in cfg['sources'] if s.get('enabled', True) and s.get('adapter') == 'telegram']
print(' '.join(ids))
")"
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') START ===" >> "$LOG"
$VENV -m crawler $EXTRA_ARGS >> "$LOG" 2>&1
EXIT_CODE=$?
echo "=== $(date '+%Y-%m-%d %H:%M:%S') END (exit=$EXIT_CODE) ===" >> "$LOG"

exit $EXIT_CODE
