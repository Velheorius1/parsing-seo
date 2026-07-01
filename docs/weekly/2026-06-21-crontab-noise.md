# Crontab noise reduction — 2026-06-21

VPS `root@46.62.155.190` crontab (not in git). Backup before change: `/tmp/cron.bak` on VPS.

## Applied (both confirmed by Daniyar)
| Line | Job | Before | After | Effect |
|------|-----|--------|-------|--------|
| 19 | `healthcheck --fix --telegram` | `0 */6 * * *` (4×/day) | `0 6 * * *` (1×/day) | −3 health msgs/day. FAIL still alerts (4h dedup intact). |
| 9 | `curl cooperation.uz && TG "cooperation.uz ONLINE"` | `0 */2 * * *` (**12×/day**) | `30 6 * * *` (1×/day) | −11 msgs/day. (Daniyar chose reduce, not remove, 2026-06-22.) |

**Net: daily platform-status noise 16 → 2 msgs/day (−87%).** freshness_watchdog still covers actual source death.

## 2026-06-30 — health/status reports daily → WEEKLY (Daniyar: «сделай раз в неделю»)
The 4 always-send status reports (that fired even when everything was fine) → weekly; failure-only alerts stay immediate.
| Job | Before | After | How |
|-----|--------|-------|-----|
| `metrics_tracker` TG report | daily 00:00 | **weekly** (via Claude routine Mon) | cron: dropped `--telegram` (kept daily `--save` so the routine reads it) |
| `healthcheck` status | daily 06:00 (always) | **fail-only** daily + weekly digest via routine | cron: `--telegram` → `--alert-on-fail` (sends only on FAIL + recovery, 4h dedup) |
| `cooperation.uz ONLINE` ping | daily 06:30 | **weekly** Mon | cron: `30 6 * * *` → `30 6 * * 1` |
| `ai_evaluator` «Анализ качества» | daily post-crawl | **weekly** (ISO-week) | code: gate `%Y-%m-%d` → `%G-W%V` (commit 78fa257) |

**Net: daily health/status messages ≈ 4 → 0** (only real failures alert). Weekly digest = Claude routine (Mon) + ai_evaluator + cooperation ping.

## Failure-only alerts kept immediate (signal, not noise)
- `exchanges_audit --only-fail`, `freshness_watchdog` (source death/revival), `supabase_token_check` (401/5xx), `openrouter_credit_check` (low balance), `healthcheck --alert-on-fail`, `proxy_health_check` (both targets down).

## Existing weekly learning crons
- Line 25: `refine_patterns --days 7 --send` Mon 09:00 · Line 93: `playbook_refine --days 7 --send` Mon 10:00 — feed the weekly routine.

## Note on existing weekly learning crons (relevant to Phase 4)
- Line 25: `refine_patterns --days 7 --send` — Mon 09:00 (regex pattern suggestions from feedback)
- Line 93: `playbook_refine --days 7 --send` — Mon 10:00 (classifier_playbook distillation — **exists**, confirms history)
These feed the weekly self-improvement routine.
