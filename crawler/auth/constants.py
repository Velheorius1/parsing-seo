"""Shared constants for auth subsystem.

All cross-module ``crawler_settings`` keys that are written/read by more than
one process (Mac daemon, VPS crawler, healthcheck, alerters) live here.
See ``.conventions/gold-standards/crawler-settings-key.py`` for the pattern.
"""

# Mac E-IMZO daemon — written each cycle (end-of-cycle JSON payload).
# Consumed by crawler/scripts/healthcheck.py.
HEARTBEAT_KEY = "eimzo_daemon_heartbeat"

# healthcheck.py alert storm dedup state (RISK-6).
# JSON: {"signature": "<sorted FAIL component names>", "alerted_at": ISO8601}.
ALERT_STATE_KEY = "healthcheck_alert_state"

# healthcheck.py daemon flap detection state.
# JSON: {"observed": [{"instance_id": uuid, "seen_at": ISO8601}, ...]}.
DAEMON_INSTANCE_STATE_KEY = "healthcheck_daemon_instance_state"

# crawler/core/zero_result_tracker.py — per-source zero-result state (task #6, RISK-1).
# JSON shape: {
#   "version": 1,
#   "sources": {
#     "<source_id>": {
#       "cycles_observed": int,        # total runs since first-seen
#       "consecutive_zeros": int,      # reset to 0 on ok_with_data
#       "last_outcome": "ok_with_data|ok_empty|skipped_no_auth|error",
#       "last_observed_at": ISO8601,
#       "alerted": bool,               # True after first alert; reset on recovery
#       "alerted_at": ISO8601 | null,
#     }
#   }
# }
ZERO_RESULT_STATE_KEY = "zero_result_state"
