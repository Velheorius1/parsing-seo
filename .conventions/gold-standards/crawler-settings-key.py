"""Gold standard: cross-module crawler_settings key.

When multiple processes (Mac daemon ↔ VPS crawler ↔ healthcheck ↔ alerters)
need to share state through the ``crawler_settings`` Supabase table, always:

1. Declare the key name as a CONSTANT in ``crawler/auth/constants.py``
   (never a string literal in the reader/writer).
2. Read/write via ``session_store.get_setting(key)`` / ``set_setting(key, dict)``.
   Never open a second Supabase client — see
   ``.conventions/anti-patterns/no-direct-supabase.md``.
3. Store JSON dicts only. Stringify/parse happens inside session_store.
4. Never log the VALUE (may contain secrets). Log only the KEY name.

Precedents in-repo: ``HEARTBEAT_KEY`` (daemon → healthcheck),
``ALERT_STATE_KEY`` (healthcheck dedup state, RISK-6),
``DAEMON_INSTANCE_STATE_KEY`` (daemon flap detection, RISK-2 consumer side).
"""

# ── crawler/auth/constants.py ───────────────────────────────────────────────
# One-line docstring per key: who writes, who reads, payload shape.

MY_FEATURE_STATE_KEY = "my_feature_state"
# Written by: crawler/scripts/my_feature.py (hourly cron)
# Read by:    crawler/scripts/healthcheck.py
# Payload:    {"last_run_at": ISO8601, "last_status": "ok|warn|fail"}


# ── Writer example (typical hourly cron or daemon loop) ─────────────────────
def write_state():
    # type: () -> bool
    from crawler.auth.constants import MY_FEATURE_STATE_KEY  # noqa: E402
    from crawler.auth.session_store import session_store

    payload = {"last_run_at": "2026-04-18T09:00:00Z", "last_status": "ok"}
    # set_setting returns True on success, False on Supabase error.
    # Do NOT raise — caller decides whether a failed write is fatal.
    return session_store.set_setting(MY_FEATURE_STATE_KEY, payload)


# ── Reader example (healthcheck consumer) ───────────────────────────────────
def read_state():
    # type: () -> dict
    from crawler.auth.constants import MY_FEATURE_STATE_KEY  # noqa: E402
    from crawler.auth.session_store import session_store

    # get_setting returns None if: missing, not JSON, not a dict, Supabase down.
    # Always coalesce to an empty dict so downstream access is safe.
    return session_store.get_setting(MY_FEATURE_STATE_KEY) or {}


# ── Anti-examples (see anti-patterns/ for more) ─────────────────────────────
# BAD: string literal duplicated in writer and reader
#   session_store.set_setting("my_feature_state", {...})   # typo risk, grep miss
#
# BAD: direct Supabase client
#   supabase = create_client(SUPABASE_URL, SERVICE_KEY)    # bypasses logging
#   supabase.table("crawler_settings").upsert(...)
#
# BAD: logging the value (may leak tokens/PII)
#   logger.info("Wrote state: %s", payload)                # use key name only
