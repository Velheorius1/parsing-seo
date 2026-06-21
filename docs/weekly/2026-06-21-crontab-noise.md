# Crontab noise reduction — 2026-06-21

VPS `root@46.62.155.190` crontab (not in git). Backup before change: `/tmp/cron.bak` on VPS.

## Applied
| Line | Job | Before | After | Effect |
|------|-----|--------|-------|--------|
| 19 | `healthcheck --fix --telegram` | `0 */6 * * *` (4×/day) | `0 6 * * *` (1×/day) | −3 health msgs/day. FAIL still alerts (4h dedup intact). |

## Recommended but NOT applied (needs your OK — out of the authorized "healthcheck" scope)
| Line | Job | Current | Issue | Proposed |
|------|-----|---------|-------|----------|
| 9 | `curl cooperation.uz && TG "cooperation.uz ONLINE"` | `0 */2 * * *` (**12×/day**) | Pure "площадка работает" spam — and it's silent on *failure* (only pings on success, backwards). Redundant with `freshness_watchdog` which already detects source death. | Reduce to `30 6 * * *` (1×/day) **or remove entirely** (freshness_watchdog covers it). |

This line 9 is likely the bulk of the daily "работающих площадках" noise you flagged (12 msgs/day vs healthcheck's 4). Say the word and I'll cut it to 1×/day or remove it.

## Untouched (per your choice to keep metrics)
- Line 2: `metrics_tracker --save --compare --telegram` `0 0 * * *` — daily, kept.
- freshness_watchdog (07:00, state-change only), exchanges_audit (`--only-fail`), supabase/openrouter checks (silent on success) — already low-noise.

## Note on existing weekly learning crons (relevant to Phase 4)
- Line 25: `refine_patterns --days 7 --send` — Mon 09:00 (regex pattern suggestions from feedback)
- Line 93: `playbook_refine --days 7 --send` — Mon 10:00 (classifier_playbook distillation — **exists**, confirms history)
These feed the weekly self-improvement routine.
