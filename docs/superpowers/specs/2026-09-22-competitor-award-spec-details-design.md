# Competitor Award Specification Detail Enrichment — Design

## Goal

Make the weekly competitor-award digest explain the purchased product, not just
the tender's often-neutral title, while preserving its retrospective and
evidence-first character.

## Scope and boundaries

The all-exchange runner already identifies exact-INN competitor awards over
20,000,000 UZS. It will enrich only those already-confirmed candidates:

- **ETender Deals:** for rows with `is_win is True`, request the public
  `GET /api/common/GetTrade/{procedure_id}/0` card and summarize the
  `budget_products` line items.
- **UZEX Direct:** for rows with `is_win is True`, request the existing public
  `GET /Common/GetDirectPurchase/{procedure_id}` detail card and summarize its
  `js_details` line items.
- **Ebirja Shop:** reuse the already-bounded contract-card fields; do not add a
  second request.

Each UZEX source has an explicit maximum of 25 detail cards per weekly run.
The limit applies after exact-INN, confirmed-winner and price/currency gates;
the system never scans every deal or contract for specifications.

## Data flow

1. The existing list collectors establish source completeness and normalize an
   award.
2. The runner selects only qualified UZEX rows and attaches a compact
   `specification_text` from the public detail response. The receipt retains a
   per-source detail outcome/count so an incomplete enrichment is visible.
3. The monitor keeps the award gate unchanged. It renders at most one compact,
   sanitized specification line below the title in the weekly Telegram digest.
4. The award key and monetary winner evidence remain unchanged. The detail text
   participates in the content fingerprint so a later specification correction
   appears as a revision rather than silently disappearing.

## Failure and delivery rules

An award is independently proven by the list record and `is_win=true`; a
detail-card failure must therefore not erase it, downgrade the source to zero,
or block the weekly digest/state. The run records an explicit detail-warning
count and sends the confirmed award without the optional specification. HTTP
timeouts/schema errors are isolated per card and never call AI or Telegram
directly.

No changes are made to alert routing, keyword promotion, state threshold,
registry, source passport, cron cadence, or the meaning of `complete` list
coverage. This adds no AI calls and no credentials.

## Digest format

For an available detail, a digest event contains:

```
• <competitor> · <amount> UZS
<tender title>
Позиции: <line-item name / short description; quantity where available>
<public evidence URL>
```

The specification is normalized to one line and capped at 280 characters to
avoid an oversized Telegram digest. No detail produces the current compact
format unchanged.

## Verification

- Unit-test ETender `budget_products` parsing, Direct `js_details` parsing,
  cap enforcement, and per-card failure isolation using mocked HTTP.
- Test that only true `is_win` exact candidates invoke the detail fetcher.
- Test digest rendering with and without `specification_text`, including the
  280-character limit.
- Run existing monitor, award-normalization, manifest and new enrichment tests.
- On VPS, use a read-only receipt replay and a bounded live probe of at most
  one known public detail per UZEX source before enabling the next cron run.

## Non-goals

- Historical backfill or mass detail crawling.
- Any new urgent tender alert or automatic keyword change.
- Detail enrichment of XT/Hayot, Cooperation or Ebirja auction/tender/selection
  where the public winner evidence remains unavailable.
