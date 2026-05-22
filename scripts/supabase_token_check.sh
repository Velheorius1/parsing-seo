#!/bin/bash
# Daily Supabase service_role token healthcheck.
# Silent on success, TG alert on failure (HTTP != 200).
#
# Triggered by cron: 0 6 * * * (06:00 UTC daily).
# Reads creds from /opt/parsing-seo/.env (shared project: parsing-seo + winch-bot + brain-bot).
#
# Behavior:
# - HTTP 200 → silent (exit 0), nothing in TG
# - HTTP 401/403 → "key invalid or rotated" TG alert
# - HTTP 5xx / timeout → "Supabase unreachable" TG alert
# - Any other → alert with diagnostic body
#
# Designed per Anthropic Academy pattern "silent on success" — TG только when action needed.

set -uo pipefail

ENV_FILE="/opt/parsing-seo/.env"
ALERT="/opt/second-brain/Projects/dsbot/scripts/dsbot-alert.py"
LOG="/var/log/supabase-healthcheck.log"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log() {
    echo "[$(ts)] $*" >> "$LOG"
}

alert() {
    log "ALERT: $*"
    if [[ -x "$ALERT" ]]; then
        "$ALERT" "$*" >> "$LOG" 2>&1 || log "  → dsbot-alert.py failed"
    else
        log "  → $ALERT not executable, alert dropped"
    fi
}

if [[ ! -f "$ENV_FILE" ]]; then
    alert "🔴 Supabase healthcheck: $ENV_FILE not found"
    exit 1
fi

SUPABASE_URL=$(grep '^SUPABASE_URL=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
SUPABASE_KEY=$(grep '^SUPABASE_SERVICE_ROLE_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")

if [[ -z "$SUPABASE_URL" || -z "$SUPABASE_KEY" ]]; then
    alert "🔴 Supabase healthcheck: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing in $ENV_FILE"
    exit 1
fi

# Lightweight ping: read 1 row from tenders (известно что таблица существует и большая)
HTTP=$(curl -s -o /tmp/supabase_check_body.tmp -w "%{http_code}" \
    --max-time 15 \
    -H "apikey: $SUPABASE_KEY" \
    -H "Authorization: Bearer $SUPABASE_KEY" \
    "$SUPABASE_URL/rest/v1/tenders?select=id&limit=1" 2>/dev/null || echo "TIMEOUT")

BODY=$(head -c 200 /tmp/supabase_check_body.tmp 2>/dev/null || echo "")
rm -f /tmp/supabase_check_body.tmp

case "$HTTP" in
    200)
        # Silent success
        log "OK (HTTP 200)"
        exit 0
        ;;
    401|403)
        alert "🔴 Supabase service_role key INVALID (HTTP $HTTP)

Action items:
1. Open https://supabase.com/dashboard/project/oaoehczbycrabkprazts/settings/api
2. Reveal current service_role key (or rotate)
3. Update SUPABASE_SERVICE_ROLE_KEY in:
   - /opt/parsing-seo/.env
   - /opt/winch-bot/.env
   - /opt/brain-bot/.env (if uses Supabase)
4. Recreate containers (NOT just restart):
   - cd /opt/winch-bot && docker compose up -d --force-recreate
   - cd /opt/brain-bot && docker compose up -d --force-recreate
   - parsing-seo runs via cron, picks up .env on next run

Body: ${BODY:-empty}"
        exit 1
        ;;
    TIMEOUT|000)
        alert "⚠️ Supabase unreachable (timeout/network)
URL: $SUPABASE_URL
Will retry tomorrow. If persists — check Supabase status page."
        exit 1
        ;;
    5*)
        alert "⚠️ Supabase HTTP $HTTP (service issue, not our key)
URL: $SUPABASE_URL
Body: ${BODY:-empty}
Usually transient — will retry tomorrow."
        exit 1
        ;;
    *)
        alert "🟡 Supabase healthcheck unexpected HTTP $HTTP
URL: $SUPABASE_URL
Body: ${BODY:-empty}"
        exit 1
        ;;
esac
