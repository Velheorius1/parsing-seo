#!/usr/bin/env bash
# Fetch cooperation.uz (lots/offers/plans/auction/eshop) and UZEX auctions via residential proxy
set -euo pipefail
DIR="/opt/parsing-seo"
LOG="/var/log/parsing-seo-proxy.log"
cd "$DIR"
export $(grep -E "^(SUPABASE_URL|SUPABASE_SERVICE_ROLE_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_ALERT_CHAT_ID|OPENROUTER_API_KEY|RESIDENTIAL_PROXY_URL)=" .env | xargs)
export HTTP_PROXY=$RESIDENTIAL_PROXY_URL
export HTTPS_PROXY=$RESIDENTIAL_PROXY_URL
# Shared notifier pipeline (coop unification 2026-07-22) does NOT set trust_env=False:
# keep Telegram/OpenRouter/Supabase/Vercel OFF the residential proxy. cooperation.uz
# traffic still rides HTTP(S)_PROXY above.
export NO_PROXY="api.telegram.org,openrouter.ai,.supabase.co,supabase.co,parsing-seo.vercel.app"
export no_proxy="$NO_PROXY"

# Precheck: skip silently if proxy is down. Dedicated cron proxy_health_check.sh sends the alert.
__probe() { curl -s -o /dev/null -w "%{http_code}" --max-time 15 -x "$RESIDENTIAL_PROXY_URL" "$1" 2>/dev/null || true; }
__pc=$(__probe https://api.ipify.org); __pc=${__pc:-000}
# httpbin.org (old target) was down daily -> precheck skipped tender fetch though proxy was fine
# (20 missed runs by 2026-06-25). Reliable target + one retry + a SECOND independent
# target (icanhazip): only SKIP if BOTH ipify AND icanhazip fail, so one target being
# down never causes a false skip.
if [ "$__pc" != "200" ]; then sleep 5; __pc=$(__probe https://api.ipify.org); __pc=${__pc:-000}; fi
if [ "$__pc" != "200" ]; then __pc2=$(__probe https://icanhazip.com); __pc2=${__pc2:-000}; else __pc2=200; fi
if [ "$__pc" != "200" ] && [ "$__pc2" != "200" ]; then
  echo "=== $(date +%Y-%m-%d\ %H:%M:%S) SKIP proxy fetch: proxy HTTP $__pc/$__pc2 (both targets down) ===" >> "$LOG"
  exit 0
fi

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
