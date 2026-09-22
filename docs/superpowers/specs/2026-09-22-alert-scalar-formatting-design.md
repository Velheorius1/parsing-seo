# Alert Scalar Formatting Repair

## Goal

Prevent a valid scalar source field from making one tender alert unformattable.

## Decision

`RawTender.extra_info` keeps its current mixed-data role. Collection detail
payloads (`dict`, `list`, `tuple`, `set`) remain non-display metadata and are
skipped by the Telegram formatter. Every remaining scalar value is converted
with `str()` before Markdown escaping. This preserves display of numeric and
boolean supplier fields and renders `None` predictably instead of raising.

The change is confined to the display boundary. It does not change tender
selection, AI relevance, deduplication, storage, alert sequence, Telegram
transport, or the rule that isolates one malformed alert from later alerts.

## Verification

Add a regression test with integer, boolean, `None`, and structured values in
one `extra_info` mapping. It must format the scalar fields, omit the
structured value, and raise no exception. Run alert-detail, notifier routing,
full-crawl, and existing competitor-monitor suites.
