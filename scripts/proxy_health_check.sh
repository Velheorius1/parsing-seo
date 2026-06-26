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
PROBE_URL="${PROXY_PROBE_URL:-https://api.ipify.org}"
PROBE_URL2="${PROXY_PROBE_URL2:-https://icanhazip.com}"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }
alert() {
  log "ALERT: $1"
  if [ -x "$ALERT" ]; then
    "$ALERT" "$1" >> "$LOG" 2>&1 || log "  dsbot-alert failed"
  else
    log "  alert not executable"
  fi
}

[ -f "$ENV_FILE" ] || { alert "proxy hc: .env missing"; exit 1; }
PROXY=$(grep "^RESIDENTIAL_PROXY_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
[ -n "$PROXY" ] || { alert "proxy hc: RESIDENTIAL_PROXY_URL missing in .env"; exit 1; }

# Probe uses explicit -x proxy; the ALERT path does NOT use the proxy.
# Probe via proxy. httpbin.org was the target until 2026-06-25 — it returned 503/timeouts
# constantly (free public service, frequently down) -> false alarms blaming the proxy,
# AND the |"| echo 000 doubled curl's own "000" into "000000" (mislabeled as unexpected).
# api.ipify.org is reliable; one retry absorbs transient target blips before alerting.
_probe() { curl -s -o /dev/null -w "%{http_code}" --max-time 20 -x "$PROXY" "$1" 2>/dev/null || true; }
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
    exit 0
  fi
  [ "$HTTP2" = "402" ] && HTTP=402
fi

case "$HTTP" in
  200)
    log "OK (proxy 200)"
    exit 0
    ;;
  402)
    alert "🔴 IPRoyal прокси CRITICAL: HTTP 402 — residential трафик исчерпан.

cooperation.uz + UZEX обратные аукционы НЕ собираются (0 новых тендеров).

Action:
1. Пополни трафик: https://dashboard.iproyal.com -> Residential -> Buy traffic (GB)
2. Проверь: ssh root@46.62.155.190 'bash /opt/parsing-seo/scripts/proxy_health_check.sh; tail -2 /var/log/proxy-healthcheck.log'"
    exit 1
    ;;
  000)
    alert "⚠️ IPRoyal прокси: timeout/000 — geo.iproyal.com не отвечает. Повтор через 3ч; если держится — проверь dashboard.iproyal.com"
    exit 1
    ;;
  *)
    alert "🟡 IPRoyal прокси: неожиданный HTTP $HTTP (probe $PROBE_URL)"
    exit 1
    ;;
esac
