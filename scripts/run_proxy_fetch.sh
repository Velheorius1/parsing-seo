#!/usr/bin/env bash
# Fetch cooperation.uz lots/offers and UZEX auctions via residential proxy
set -euo pipefail
DIR="/opt/parsing-seo"
LOG="/var/log/parsing-seo-proxy.log"
cd "$DIR"
export $(grep -E "^(SUPABASE_URL|SUPABASE_SERVICE_ROLE_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_ALERT_CHAT_ID|OPENROUTER_API_KEY|RESIDENTIAL_PROXY_URL)=" .env | xargs)
export HTTP_PROXY=$RESIDENTIAL_PROXY_URL
export HTTPS_PROXY=$RESIDENTIAL_PROXY_URL

echo "=== $(date +%Y-%m-%d\ %H:%M:%S) START proxy fetch ===" >> "$LOG"

# Cooperation.uz lots (reverse tenders) + offers
.venv/bin/python3 scripts/fetch_cooperation.py --source lots >> "$LOG" 2>&1
.venv/bin/python3 scripts/fetch_cooperation.py --source offers >> "$LOG" 2>&1

# UZEX auctions + prequalifications
.venv/bin/python3 scripts/fetch_uzex_auctions.py >> "$LOG" 2>&1

echo "=== $(date +%Y-%m-%d\ %H:%M:%S) END proxy fetch ===" >> "$LOG"
