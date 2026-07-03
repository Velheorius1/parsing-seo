#!/bin/bash
# Synthetic-probe healthcheck for IPRoyal residential proxy.
# Silent on success (HTTP 200 via proxy). DIRECT (non-proxy) Telegram alert on 402/000/other.
# Cron every 3h. Reads RESIDENTIAL_PROXY_URL from /opt/parsing-seo/.env.
# Context: 2026-06-01..04 IPRoyal traffic ran out -> 402 everywhere -> 3.5d silent outage
# (the proxy-fetch alert itself went through the dead proxy). This catches it within 3h.
set -uo pipefail
ENV_FILE="/opt/parsing-seo/.env"
ALERT="/opt/second-brain/Projects/dsbot/scripts/dsbot-alert.py"
LOG="/var/log/proxy-healthcheck.log"
STATE="/var/lib/proxy-healthcheck.state"   # last alerted code + epoch — throttle
THROTTLE_SECS="${PROXY_HC_THROTTLE:-43200}" # re-alert same condition at most once / 12h
PROBE_URL="${PROXY_PROBE_URL:-https://api.ipify.org}"
PROBE_URL2="${PROXY_PROBE_URL2:-https://icanhazip.com}"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Throttle: only alert if the code CHANGED or THROTTLE_SECS passed since last same-code
# alert. Daniyar flagged the every-3h spam («прекрати алерты») — a persistent unresolved
# outage should ping ~2x/day, not 8x. State clears on recovery so the next fault re-arms.
should_alert() {
  local code="$1" now last_code last_ts
  now=$(date +%s)
  if [ -f "$STATE" ]; then read -r last_code last_ts < "$STATE" 2>/dev/null || true; fi
  if [ "$code" = "${last_code:-}" ] && [ $((now - ${last_ts:-0})) -lt "$THROTTLE_SECS" ]; then
    return 1
  fi
  mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
  echo "$code $now" > "$STATE"
  return 0
}

alert() {
  local code="$1" msg="$2"
  if ! should_alert "$code"; then
    log "THROTTLED ($code within ${THROTTLE_SECS}s): ${msg%%$'\n'*}"
    return 0
  fi
  log "ALERT: ${msg%%$'\n'*}"
  if [ -x "$ALERT" ]; then
    "$ALERT" "$msg" >> "$LOG" 2>&1 || log "  dsbot-alert failed"
  else
    log "  alert not executable"
  fi
}

[ -f "$ENV_FILE" ] || { alert other "proxy hc: .env missing"; exit 1; }
PROXY=$(grep "^RESIDENTIAL_PROXY_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
[ -n "$PROXY" ] || { alert other "proxy hc: RESIDENTIAL_PROXY_URL missing in .env"; exit 1; }

# Probe uses explicit -x proxy; the ALERT path does NOT use the proxy.
# httpbin.org was the target until 2026-06-25 — it returned 503/timeouts constantly
# (free public service) -> false alarms blaming the proxy. api.ipify.org is reliable.
#
# CRITICAL (2026-07-03): traffic-exhaustion (402) and auth (407) surface at the CONNECT
# TUNNEL stage, where curl exits 56 and %{http_code} stays 000 — the message
# "CONNECT tunnel failed, response NNN" carries the real code. Parse it, else the
# 402 case is misread as a 000 "gateway timeout" and the wrong (non-actionable) alert
# fires. _probe returns the effective code (real http_code OR the CONNECT response code).
_probe() {
  local err code connect
  err="$(mktemp)"
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 -x "$PROXY" "$1" 2>"$err") || true
  code="${code:-000}"
  if [ "$code" = "000" ]; then
    connect=$(sed -nE 's/.*CONNECT tunnel failed, response ([0-9]{3}).*/\1/p' "$err" | head -1)
    [ -n "$connect" ] && code="$connect"
  fi
  rm -f "$err"
  echo "$code"
}

HTTP=$(_probe "$PROBE_URL"); HTTP=${HTTP:-000}
if [ "$HTTP" != "200" ] && [ "$HTTP" != "402" ]; then sleep 5; HTTP=$(_probe "$PROBE_URL"); HTTP=${HTTP:-000}; fi
# Cross-check a SECOND independent target before blaming the proxy. A single free
# target being down (the httpbin lesson) must NOT trigger a false alarm — only if
# BOTH ipify AND icanhazip fail via the proxy do we conclude the proxy is down.
# (402 stays authoritative: it comes from the proxy itself = traffic exhausted.)
if [ "$HTTP" != "200" ] && [ "$HTTP" != "402" ]; then
  HTTP2=$(_probe "$PROBE_URL2"); HTTP2=${HTTP2:-000}
  if [ "$HTTP2" = "200" ]; then
    log "OK (proxy 200 via fallback $PROBE_URL2; $PROBE_URL gave $HTTP — target blip, not proxy)"
    [ -f "$STATE" ] && rm -f "$STATE"   # recovery — re-arm alerts
    exit 0
  fi
  [ "$HTTP2" = "402" ] && HTTP=402
fi

case "$HTTP" in
  200)
    log "OK (proxy 200)"
    [ -f "$STATE" ] && rm -f "$STATE"   # recovery — re-arm alerts
    exit 0
    ;;
  402)
    alert 402 "🔴 IPRoyal прокси CRITICAL: HTTP 402 — residential трафик исчерпан.

cooperation.uz + UZEX обратные аукционы НЕ собираются (0 новых тендеров).

Action:
1. Пополни трафик: https://dashboard.iproyal.com -> Residential -> Buy traffic (GB)
2. Проверь: ssh root@46.62.155.190 'bash /opt/parsing-seo/scripts/proxy_health_check.sh; tail -2 /var/log/proxy-healthcheck.log'"
    exit 1
    ;;
  000)
    alert 000 "⚠️ IPRoyal прокси: timeout/000 — geo.iproyal.com не отвечает. Повтор через 12ч; если держится — проверь dashboard.iproyal.com"
    exit 1
    ;;
  *)
    alert "$HTTP" "🟡 IPRoyal прокси: неожиданный HTTP $HTTP (probe $PROBE_URL)"
    exit 1
    ;;
esac
