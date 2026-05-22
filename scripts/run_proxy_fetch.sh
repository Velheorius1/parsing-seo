#!/usr/bin/env bash
# Fetch cooperation.uz (lots/offers/plans/auction/eshop) and UZEX auctions via residential proxy
set -euo pipefail
DIR="/opt/parsing-seo"
LOG="/var/log/parsing-seo-proxy.log"
cd "$DIR"
export $(grep -E "^(SUPABASE_URL|SUPABASE_SERVICE_ROLE_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_ALERT_CHAT_ID|OPENROUTER_API_KEY|RESIDENTIAL_PROXY_URL)=" .env | xargs)
export HTTP_PROXY=$RESIDENTIAL_PROXY_URL
export HTTPS_PROXY=$RESIDENTIAL_PROXY_URL

echo "=== $(date +%Y-%m-%d\ %H:%M:%S) START proxy fetch ===" >> "$LOG"

# Cooperation.uz — lots+offers (high-volume) + plans/auction/eshop (resurrected 2026-04-28)
# Each source isolated in its own subshell so one failure doesn't kill the rest
( .venv/bin/python3 scripts/fetch_cooperation.py --source lots    || echo 'lots FAILED' )    >> "$LOG" 2>&1
( .venv/bin/python3 scripts/fetch_cooperation.py --source offers  || echo 'offers FAILED' )  >> "$LOG" 2>&1
( .venv/bin/python3 scripts/fetch_cooperation.py --source plans   || echo 'plans FAILED' )   >> "$LOG" 2>&1
( .venv/bin/python3 scripts/fetch_cooperation.py --source auction || echo 'auction FAILED' ) >> "$LOG" 2>&1
( .venv/bin/python3 scripts/fetch_cooperation.py --source eshop   || echo 'eshop FAILED' )   >> "$LOG" 2>&1
# Contracts: closed auction deals via stat-new.cooperation.uz (added 2026-05-22).
# Public endpoint with full customer+producer+prices — replaces blocked E-IMZO path.
( .venv/bin/python3 scripts/fetch_cooperation.py --source contracts || echo 'contracts FAILED' ) >> "$LOG" 2>&1

# UZEX auctions + prequalifications
( .venv/bin/python3 scripts/fetch_uzex_auctions.py || echo 'uzex FAILED' ) >> "$LOG" 2>&1

echo "=== $(date +%Y-%m-%d\ %H:%M:%S) END proxy fetch ===" >> "$LOG"
