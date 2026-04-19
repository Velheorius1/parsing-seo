"""Gold standard: add a new check to crawler/scripts/healthcheck.py.

Healthcheck runs hourly via cron (`--alert-on-fail`). Each check is a method
on ``HealthCheck`` returning a ``CheckResult(component, status, detail)``.

Statuses (canonical order):
    OK   — healthy, nothing to report.
    WARN — degraded, no page. Included in periodic digest only.
    FAIL — actionable, pages operator via TG alert (respecting dedup, RISK-6).
    UNKNOWN — cascade-suppressed (upstream dep, e.g. Supabase, is FAIL).
    FIXED — transient: was FAIL last run, OK now. Used by recovery messaging.

Keep checks CHEAP (<5s). Expensive probes belong elsewhere (separate cron).
"""

from typing import Optional


class HealthCheck:
    """Simplified view of crawler/scripts/healthcheck.py::HealthCheck."""

    def check_my_component(self):
        # type: () -> CheckResult
        """One-line summary: what failure here means operationally."""
        # 1. Cheap probe — short timeout, tight error scope.
        try:
            ok = self._probe()  # returns bool or raises
        except Exception as exc:
            # Log the class + 80-char tail only. Never dump full response.
            self._log.warning("my_component probe error: %s", str(exc)[:80])
            return self._result("my_component", FAIL, "probe error")

        # 2. Translate probe result to status.
        if ok:
            return self._result("my_component", OK, "healthy")
        return self._result("my_component", WARN, "degraded but not critical")

    # ── If the check depends on Supabase ────────────────────────────────
    # Add your component name to SUPABASE_DEPENDENT_COMPONENTS (healthcheck.py).
    # When Supabase itself FAILs, this check is rendered as UNKNOWN — keeps the
    # alert signature stable so RISK-6 dedup works.

    # ── If the check needs shared state (state across runs) ─────────────
    # Use session_store.get_setting / set_setting with a const key from
    # crawler/auth/constants.py. See
    # .conventions/gold-standards/crawler-settings-key.py.

    # ── Wire into main() ────────────────────────────────────────────────
    # In run_all(): self.results.append(self.check_my_component())
    # Name must be grep-able — used in alert signatures, TG message body,
    # and --json output keys. Do NOT rename without bumping signature logic.


# ── DO / DON'T cheat sheet ──────────────────────────────────────────────────
# DO:
#   - Short timeout (httpx/requests timeout=5)
#   - Return CheckResult, never raise
#   - Log only: component name, status, counts, elapsed ms
#   - If check touches tokens: see anti-patterns/no-token-leakage.md
#
# DON'T:
#   - Open a new Supabase client — use session_store (no-direct-supabase.md)
#   - Send TG directly from a check — main() aggregates and dedups (RISK-6)
#   - Put check-specific constants inline — move to constants.py if >1 reader
