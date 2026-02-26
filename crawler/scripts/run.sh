#!/usr/bin/env bash
# Run crawler once (for external cron or manual invocation)
# Usage: ./scripts/run.sh [--dry-run] [--sources etender world-bank]
#
# Add to VPS crontab:
# 0 */2 * * * cd /opt/tender-crawler && docker compose run --rm crawler >> /var/log/tender-crawler.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "$(date) [RUN] Starting crawler..."
docker compose run --rm crawler python -m crawler.main "$@"
echo "$(date) [RUN] Crawler finished."
