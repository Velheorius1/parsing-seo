# Crontab noise reduction — 2026-06-21

VPS `root@46.62.155.190` crontab (not in git). Backup before change: `/tmp/cron.bak` on VPS.

## Applied (both confirmed by Daniyar)
| Line | Job | Before | After | Effect |
|------|-----|--------|-------|--------|
| 19 | `healthcheck --fix --telegram` | `0 */6 * * *` (4×/day) | `0 6 * * *` (1×/day) | −3 health msgs/day. FAIL still alerts (4h dedup intact). |
| 9 | `curl cooperation.uz && TG "cooperation.uz ONLINE"` | `0 */2 * * *` (**12×/day**) | `30 6 * * *` (1×/day) | −11 msgs/day. (Daniyar chose reduce, not remove, 2026-06-22.) |

**Net: daily platform-status noise 16 → 2 msgs/day (−87%).** freshness_watchdog still covers actual source death.

## Untouched (per your choice to keep metrics)
- Line 2: `metrics_tracker --save --compare --telegram` `0 0 * * *` — daily, kept.
- freshness_watchdog (07:00, state-change only), exchanges_audit (`--only-fail`), supabase/openrouter checks (silent on success) — already low-noise.

## Note on existing weekly learning crons (relevant to Phase 4)
- Line 25: `refine_patterns --days 7 --send` — Mon 09:00 (regex pattern suggestions from feedback)
- Line 93: `playbook_refine --days 7 --send` — Mon 10:00 (classifier_playbook distillation — **exists**, confirms history)
These feed the weekly self-improvement routine.
