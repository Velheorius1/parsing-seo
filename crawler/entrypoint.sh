#!/usr/bin/env bash
# Entrypoint: run crawler once on start, then keep cron running
set -e

echo "$(date) [ENTRYPOINT] Running initial crawl..."
cd /app && python -m crawler.main 2>&1 | tee -a /var/log/crawler.log

echo "$(date) [ENTRYPOINT] Starting cron daemon (every 2 hours)..."
# Pass environment to cron jobs
printenv | grep -E '^(SUPABASE_|TELEGRAM_|DRY_RUN|LOG_LEVEL)' > /etc/environment
cron && tail -f /var/log/crawler.log
