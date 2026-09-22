# Confirmed competitor award winner gate — design

## Problem

The weekly competitor monitor accepts UZEX award-shaped rows when their winner
INN, amount, and currency are present. A saved ETender receipt contains one row
for PECHATNIK VOSTOKA with `is_win=false` and an unknown status. Its presence
would create a false competitor-win digest event even though the same collector
already exposes the authoritative `is_win` field.

## Decision

For UZEX/ETender collector rows, an award qualifies only when `is_win is True`.
The existing exact-INN, strictly-above-20m, UZS-currency, and contract-ID gates
remain mandatory. The existing `final_total -> amount` and `evidence_url ->
source_url` normalization remains unchanged.

The condition is scoped to rows that carry the collector's `is_win` field;
non-UZEX public source adapters retain their own evidence contracts. An explicit
`False` or missing/unknown UZEX win outcome is never promoted to state or digest.

## Verification

Add regression fixtures proving that a UZEX row with a valid competitor INN and
price but `is_win=false` is excluded, while identical `is_win=true` data remains
qualified. Replay the saved ETender receipt without state advancement or Telegram
delivery: it must produce three confirmed PECHATNIK VOSTOKA awards, not four.

## Non-goals

- No changes to competitor registry, aliases, source passport, polling, crawler
  alerts, delivery wording, cron schedule, or AI usage.
- No state write, Telegram delivery, or live collector run during replay.
