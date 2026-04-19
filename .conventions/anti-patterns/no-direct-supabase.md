# Anti-pattern: opening a direct Supabase client in daemon / healthcheck / scripts

Context: `session_store` (crawler/auth/session_store.py) is the **single gateway** to the `crawler_settings` table. One client, one logging policy, one error-handling path. See DECISIONS.md entry "`session_store.set_setting()` is the canonical API".

## Forbidden

```python
# BAD: new client inside a script or daemon
from supabase import create_client
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
supabase.table("crawler_settings").upsert({"key": "my_state", "value": json.dumps(x)}).execute()
```

Why bad:
- Credential handling duplicated (risk of hardcoded keys — CLAUDE.md Tier 3)
- Logs bypass `[SessionStore]` prefix; grep-hunts for storage issues miss this call site
- `crawler_settings` key name becomes a string literal, not a `constants.py` const → typo risk, silent drift
- Error handling is ad-hoc (some call sites raise, others swallow)

## Required

```python
from crawler.auth.constants import MY_STATE_KEY  # declared once, imported everywhere
from crawler.auth.session_store import session_store

# Write (returns bool — False on any error, already logged inside session_store)
session_store.set_setting(MY_STATE_KEY, {"last_run_at": "...", "ok": True})

# Read (returns dict or None — coalesce to {} for safe access)
state = session_store.get_setting(MY_STATE_KEY) or {}
```

See `.conventions/gold-standards/crawler-settings-key.py` for the full pattern.

## Exceptions

The rule is about `crawler_settings` keys. Bulk data tables (`tenders`,
`feedback_logs`, `results_tracker_*`) use their own specialized modules
(`crawler/core/db.py`, `crawler/core/results_tracker.py`). Those modules are
allowed to open their own client — they are the gateway for their table,
just like `session_store` is the gateway for `crawler_settings`.

## DoD gate

```bash
# No raw create_client in scripts/healthcheck/daemon
git grep -n "create_client" crawler/scripts crawler/auth_eimzo.py
# Expected output: zero lines (only session_store.py imports it).
```
