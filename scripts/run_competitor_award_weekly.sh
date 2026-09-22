#!/usr/bin/env bash
# Read-only all-exchange competitor award monitor. No Telegram delivery.
set -euo pipefail
cd /opt/parsing-seo
data_dir=/opt/parsing-seo/data/competitor-award-monitor
run_at=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$data_dir/receipts"
date_from=$(date -u -d '30 days ago' +%F)
# Cooperation blocks direct datacenter traffic. Reuse only its existing
# residential proxy; do not proxy Telegram or the other public exchanges.
proxy_assignment=$(grep -m1 '^RESIDENTIAL_PROXY_URL=' .env || true)
if [ -n "$proxy_assignment" ]; then
  export "COMPETITOR_COOP_PROXY_URL=${proxy_assignment#RESIDENTIAL_PROXY_URL=}"
fi
.venv/bin/python3 -m crawler.scripts.run_all_exchange_competitor_monitor \
  --date-from "$date_from" --page-size 100 --page-cap 50 --max-details 25 \
  --output "$data_dir/receipts/$run_at-sources.json"
.venv/bin/python3 -m crawler.scripts.monitor_competitor_awards \
  --source-runs "$data_dir/receipts/$run_at-sources.json" \
  --state "$data_dir/state.json" \
  --outbox "$data_dir/outbox.json" \
  --output "$data_dir/receipts/$run_at-delta.json" --send-telegram --advance-state
