#!/bin/bash
# Daily OpenRouter credit healthcheck.
# Silent on success (balance > $0.50), TG alert on low/empty balance OR auth failure.
#
# Triggered by cron: 5 6 * * * (06:05 UTC daily, после Supabase healthcheck).
# Reads creds from /opt/parsing-seo/.env (OPENROUTER_API_KEY).
#
# Why this exists:
# - 19.05.2026 balance ушёл в 0 → AI relevance silent-fallback к `_allow()` → мусор в TG
# - 26.05.2026 повторное обнуление → 74% AI calls возвращали HTTP 402 в compare report
# - Supabase healthcheck не покрывает OpenRouter (отдельный provider)
#
# Behavior:
# - balance > $0.50 → silent (exit 0)
# - balance <= $0.50 → 🟡 LOW warning (через 1-2 дня закончится)
# - balance <= $0.10 → 🔴 CRITICAL (AI фильтрация ломается прямо сейчас)
# - HTTP 401/403 → "API key invalid or rotated"
# - HTTP 5xx / timeout → "OpenRouter unreachable"

set -uo pipefail

ENV_FILE="/opt/parsing-seo/.env"
ALERT="/opt/second-brain/Projects/dsbot/scripts/dsbot-alert.py"
LOG="/var/log/openrouter-healthcheck.log"

# Thresholds in USD (after OpenRouter free credits exhausted, paid calls draw from this)
LOW_THRESHOLD=5.00      # ранний warn: ~5-6 дней рантайма при текущем burn (~$1/день). Было 0.50 = <1 дня — поздно
CRITICAL_THRESHOLD=2.00 # было 0.10 — AI падал в fail-open до того как Данияр успевал пополнить (3 обнуления/мес)

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
    alert "🔴 OpenRouter healthcheck: $ENV_FILE not found"
    exit 1
fi

OPENROUTER_KEY=$(grep '^OPENROUTER_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")

if [[ -z "$OPENROUTER_KEY" ]]; then
    alert "🔴 OpenRouter healthcheck: OPENROUTER_API_KEY missing in $ENV_FILE"
    exit 1
fi

# Query OpenRouter credits endpoint (returns balance + usage limit)
HTTP=$(curl -s -o /tmp/openrouter_check_body.tmp -w "%{http_code}" \
    --max-time 15 \
    -H "Authorization: Bearer $OPENROUTER_KEY" \
    "https://openrouter.ai/api/v1/credits" 2>/dev/null || echo "TIMEOUT")

BODY=$(head -c 500 /tmp/openrouter_check_body.tmp 2>/dev/null || echo "")
rm -f /tmp/openrouter_check_body.tmp

case "$HTTP" in
    200)
        # Parse balance — endpoint returns: {"data": {"total_credits": N, "total_usage": M}}
        # Available = total_credits - total_usage
        BALANCE=$(echo "$BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin).get('data', {})
    available = float(d.get('total_credits', 0)) - float(d.get('total_usage', 0))
    print(f'{available:.4f}')
except Exception as e:
    print(f'PARSE_ERROR: {e}', file=sys.stderr)
    print('0')
" 2>/dev/null)

        if [[ -z "$BALANCE" ]] || ! [[ "$BALANCE" =~ ^[0-9.-]+$ ]]; then
            alert "🟡 OpenRouter healthcheck: cannot parse balance
Body: $BODY"
            exit 1
        fi

        # Float compare via bc / awk
        IS_CRITICAL=$(awk -v b="$BALANCE" -v t="$CRITICAL_THRESHOLD" 'BEGIN{print (b<=t)?1:0}')
        IS_LOW=$(awk -v b="$BALANCE" -v t="$LOW_THRESHOLD" 'BEGIN{print (b<=t)?1:0}')

        if [[ "$IS_CRITICAL" == "1" ]]; then
            alert "🔴 OpenRouter CRITICAL: balance = \$$BALANCE

AI relevance фильтрация СЕЙЧАС не работает — все calls возвращают HTTP 402.
Тендеры проходят через _allow() → мусор летит в TG.

Action:
1. Пополни баланс: https://openrouter.ai/settings/credits
2. Рекомендуемая сумма: \$10 (хватит на ~50K AI calls)
3. После пополнения проверь:
   ssh root@46.62.155.190 'bash /opt/parsing-seo/scripts/openrouter_credit_check.sh && cat /var/log/openrouter-healthcheck.log | tail -3'"
            exit 1
        elif [[ "$IS_LOW" == "1" ]]; then
            alert "🟡 OpenRouter LOW: balance = \$$BALANCE

Закончится за 1-2 дня. AI фильтрация сломается тихо (silent _allow fallback).

Action: пополни баланс https://openrouter.ai/settings/credits (рекомендуется \$10+)."
            log "LOW (balance=\$$BALANCE)"
            exit 0
        else
            log "OK (balance=\$$BALANCE)"
            exit 0
        fi
        ;;
    401|403)
        alert "🔴 OpenRouter API key INVALID (HTTP $HTTP)

Action:
1. Open https://openrouter.ai/settings/keys
2. Rotate or copy current key
3. Update OPENROUTER_API_KEY in /opt/parsing-seo/.env
4. Cron picks up .env on next run

Body: ${BODY:-empty}"
        exit 1
        ;;
    TIMEOUT|000)
        alert "⚠️ OpenRouter unreachable (timeout/network)
Will retry tomorrow. If persists — check https://status.openrouter.ai/"
        exit 1
        ;;
    5*)
        alert "⚠️ OpenRouter HTTP $HTTP (service issue)
Body: ${BODY:-empty}
Usually transient — will retry tomorrow."
        exit 1
        ;;
    *)
        alert "🟡 OpenRouter healthcheck unexpected HTTP $HTTP
Body: ${BODY:-empty}"
        exit 1
        ;;
esac
