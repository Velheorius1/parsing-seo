# STANDARD POLIGRAF BOOKS INN Registry Design

## Goal

Close the only unresolved company from the supplied competitor list while
preserving the system's exact-INN identity rule.

## Evidence and identity decision

`STANDARD POLIGRAF BOOKS` is a separate legal entity with INN `305970088`.
The public company directory entry at Statsnet supplies the INN; GoldenPages
and Sprav list Books separately from `STANDARD POLIGRAF SERVICE`, with a
distinct contact number.  A shared Foziltepa 12B address is therefore not a
reason to inherit Service's INN `207063624`.

The registry will retain two independent entries:

| Entity | INN | Identity rule |
| --- | --- | --- |
| STANDARD POLIGRAF SERVICE | 207063624 | Existing exact identity, unchanged |
| STANDARD POLIGRAF BOOKS | 305970088 | New exact identity, never merged with Service |

`CENTRIS-PRINT` stays an independent candidate. It is already included by the
registry's default INN list, so this release does not relocate or merge it.

## Change

Update only `crawler/config/competitor_registry.json`:

- replace Books' unresolved `null` INN with `305970088`;
- add transliteration aliases used in public records;
- replace the unresolved status and source list with the corroborating
  directories and the explicit non-merge note.

Add a focused registry test that verifies Books resolves only through its own
INN and that Service keeps its distinct INN.

## Safety and observability

The collectors, amount threshold (`> 20,000,000 UZS`), source passports,
weekly schedule, Telegram delivery, and AI usage do not change.  All source
paths continue matching award winners by exact INN only.  The new record can
therefore expand coverage only when a source exposes `305970088`; it cannot
attribute a Service award to Books or match by a fuzzy name.

## Verification

Run the registry audit unit tests plus the award-monitor, award-audit, and
source-manifest suites. Validate that the loaded registry contains both unique
INNs and that `registry_inns()` includes Books.
