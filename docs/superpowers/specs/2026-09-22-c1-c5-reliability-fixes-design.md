# C1–C5 reliability fixes design

Date: 2026-09-22
Status: approved in conversation
Scope: close the seven reproduced integration gaps left after C1–C5

## Objective

Preserve the useful C1–C5 architecture while closing the remaining data-loss,
false-delta and delivery-retry cases. Changes ship as four isolated releases so
each root cause can be tested, reviewed, deployed and observed independently.

The design does not add new exchanges, change the 20,000,000 UZS threshold,
promote shadow terms into live alerts, or claim exactly-once Telegram semantics.

## Release 1: C2 detail persistence

### Required behavior

1. A newly fetched `_detail_text` is authoritative and replaces an older stored
   value for the same tender.
2. Stored detail is restored only when the incoming list payload has no fresh
   detail.
3. Pre-C2 rows that contain additional searchable text but no `_detail_text`
   receive a bounded compatibility migration: the incoming list search text is
   removed from the stored search text and only the remaining suffix is treated
   as legacy detail. If the difference cannot be established safely, no legacy
   detail is fabricated.
4. A failed existence/detail read is an unknown DB state, not evidence that the
   row is new. Tenders in the failed lookup batch are excluded from both upsert
   and `new_tenders` for that run and retried by the next crawl.
5. For prequalification lots, a fresh non-empty incoming lots payload takes
   precedence; persisted lots are restored only when the list has no fresh lots.

### Data flow

`_get_existing_rows` returns both known rows and lookup-unknown keys. The upsert
pipeline restores/migrates detail only for known rows, filters lookup-unknown
keys before newness classification and write, and logs the deferred count.

No database schema migration is required. Compatibility detail is stored in the
existing `extra_info._detail_text` field on the next safe write.

## Release 2: C5 durable delivery

### Required behavior

1. Current new/changed events are merged with a durable outbox before rendering.
2. The outbox is written atomically before Telegram delivery begins.
3. Transport, HTTP and response-decoding failures become an incomplete delivery
   result; they do not escape before receipt/state persistence.
4. After every confirmed batch, its keys are checkpointed in source state and
   removed from the outbox. State is written before the corresponding outbox
   removal so a failed second write prefers a possible duplicate over data loss.
5. Pending event payloads survive the weekly 30-day collection window and are
   retried on later runs.
6. Bootstrap remains silent and creates a baseline without filling the outbox.
7. Preview/no-send runs do not mutate the delivery outbox.

### Files and compatibility

`monitor_competitor_awards.py` gains an `--outbox` path and atomic JSON writes.
The weekly script passes a stable outbox file under the existing monitor data
directory. Existing state files remain readable; a missing outbox means empty.

Telegram cannot provide strict exactly-once semantics when a timeout happens
after accepting a message but before returning the response. The system offers
at-least-once retry with durable acknowledgements for unambiguous successes.

## Release 3: C3 pagination completeness

### Required behavior

Ebirja metadata is validated before an empty page is accepted as authoritative.
An empty page is complete only when pagination metadata says the requested page
is at or beyond the valid end, or the source authoritatively reports zero total
rows. Contradictory cases such as page 0 with `pageCount=10,totalCount=100` return
an incomplete completion reason and cannot advance source state.

## Release 4: C4 winner identity

### Required behavior

Ebirja detail identity has three outcomes:

- exact registered INN: confirmed competitor award;
- valid but unregistered INN: confirmed rejection;
- missing or invalid INN: unresolved identity.

A source run with unresolved identity is reported as `partial_identity`. Verified
awards in that run may produce new/change events, while the candidate state is a
non-destructive union of the previous state and currently verified awards.
Unknown cards therefore cannot remove an older award or make it look new after
recovery. `partial_identity` is not counted as full all-source coverage.

## Test strategy

Each release starts by converting its audit probe into a failing regression test.
Tests cover:

- fresh detail replaces stored detail and survives the next list crawl;
- legacy searchable detail is migrated conservatively;
- failed lookup neither writes nor reports the affected tender as new;
- a network exception after batch one persists batch-one progress;
- pending events outside the next 30-day snapshot are retried;
- contradictory empty pagination remains incomplete and preserves baseline;
- missing INN preserves prior history while a valid foreign INN remains rejected.

After each focused test passes, run all C1–C5 contract tests and Python compile
checks. Deploy each release separately and repeat the same offline tests on VPS.
No live Telegram message, production DB mutation or full exchange crawl is part
of the automated acceptance test.

## Release and rollback

Each release has its own commit and pull request. The next release starts only
after the previous one is merged, deployed and verified. Rollback is per release;
no release changes credentials or database schema. Existing audit receipts and
untracked source captures are not modified.
