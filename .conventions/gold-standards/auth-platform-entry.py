"""Gold standard: add a new E-IMZO-authenticated platform.

Platforms live in ``crawler/auth_eimzo.py::PLATFORMS`` (config dict) and
``crawler/scripts/mac_eimzo_daemon.py::PLATFORM_BACKENDS`` (challenge/login URLs
the daemon calls to refresh JWTs every ~4h).

Shipping a new platform happens in two phases:

Phase 1 — manual-paste baseline (no daemon automation yet).
Phase 2 — automated refresh (daemon grows a verified backend entry).

NEVER block a Phase-2 merge on Phase-2 completeness for platforms that lack
verified backend URLs — daemon warns once at startup and skips them per cycle
(see DECISIONS.md "Three of four daemon platforms ship with backends TODO").
"""

# ── Phase 1: register in auth_eimzo.PLATFORMS ───────────────────────────────
# Keys: display label, localStorage candidates (first JWT-valid wins — see
# RISK-5 mitigation in DECISIONS.md), login URL the operator opens.
PLATFORMS = {
    "my-platform": {
        "label": "My Platform (.uz)",
        "login_url": "https://my-platform.uz/auth/login",
        # Order matters — first key with a JWT-valid token wins. Unknown keys
        # are silently skipped. List ≥2 candidates if the site renames often.
        "token_keys": ["accessToken", "jwt"],
    },
    # ... other platforms
}


# ── Phase 2: register in mac_eimzo_daemon.PLATFORM_BACKENDS ─────────────────
# Only after DevTools-verified challenge + login paths. `None` entries are
# allowed and non-blocking — daemon warns once and skips.
PLATFORM_BACKENDS = {
    "my-platform": {
        "challenge_url": "https://api.my-platform.uz/auth/challenge",
        "login_url": "https://api.my-platform.uz/auth/login",
        # Request-body shape as observed in DevTools. Do NOT guess.
        "login_body_template": {"pkcs7": "{pkcs7}", "challenge": "{challenge}"},
    },
    "hayotbirja": None,   # TODO: capture via DevTools
    "xt-xarid":   None,   # TODO: capture via DevTools
}


# ── Non-negotiables (RISK-3 JWT hygiene) ────────────────────────────────────
# When writing/refreshing tokens, ONLY log platform + len + alg + exp.
# NEVER: logger.info("Token: %s", jwt), print(resp.json()), print(resp.headers)
# NEVER: include JWT in error messages, TG alerts, --json healthcheck output.
# See ``.conventions/anti-patterns/no-token-leakage.md``.


# ── Acceptance checks before merge ──────────────────────────────────────────
# 1. ``id_prefix`` for any new authed source in ``sources.yaml`` must NOT
#    collide with existing ebirja-* — RISK-4. Use suffix ``-auth-…``.
# 2. Healthcheck (``check_tokens``) must list the new platform. Add to
#    ``PLATFORMS`` iteration — don't maintain a second list.
# 3. If a backend URL is unknown at merge time: ship Phase 1 only, leave
#    ``PLATFORM_BACKENDS["my-platform"] = None``, open follow-up task.
