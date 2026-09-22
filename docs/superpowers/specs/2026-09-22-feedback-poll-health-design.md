# Feedback polling health — design

## Problem

`healthcheck` currently reports the feedback bot healthy when its systemd unit is
active and its code is fresh. On 2026-09-22, Telegram returned repeated HTTP 409
for `getUpdates`, so alert buttons were not handled even though the healthcheck
was green.

## Decision

Add an observational polling check to `HealthCheck.check_feedback_bot()`.

On a VPS, it reads only recent journal entries for `parsing-feedback-bot`:

- a `409 Conflict` in the last five minutes is `FAIL`;
- a recent `getUpdates` HTTP 200 and no conflict is `OK`;
- no matching poll result, unavailable journalctl, or an unexpected command
  failure is `WARN`, not `FAIL`.

The check creates the separate `feedback_bot.polling` component. The existing
`feedback_bot` component retains unit/stale-code responsibility, so an active
service and an actually functioning polling loop are independently visible.

## Non-goals

- Do not issue `getUpdates` from healthcheck: a probe itself could conflict with
  the live poller.
- Do not change feedback polling, Telegram token, webhook configuration,
  callbacks, database schema, cron, or auto-restart behavior.
- Do not parse historic logs beyond the five-minute diagnostic window.

## Error handling and verification

The journal reader receives its text through `subprocess.run` with a bounded
timeout. Tests mock that boundary and prove each status branch: conflict fails,
recent 200 is healthy, no evidence warns, and unavailable journal warns. The
targeted healthcheck tests and the broader feedback-related suite must pass
before commit and deployment. Deployment is a normal Git cutover only; no
service restart is required because healthcheck launches a fresh process.
