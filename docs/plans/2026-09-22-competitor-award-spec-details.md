# Competitor Award Specification Detail Enrichment Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show a bounded, evidence-backed product specification for confirmed competitor awards in the weekly digest.

**Architecture:** A dedicated UZEX detail-enrichment module parses public ETender and Direct detail cards into compact text. The all-exchange runner calls it only after the existing winner, exact-INN, currency, and threshold gates; detail failure is isolated per award. The monitor stores the compact text in its normal event fingerprint and renders it without changing award qualification.

**Tech Stack:** Python 3.9, httpx, JSON, standalone assertion test modules, Telegram text formatting.

---

### Task 1: Define pure line-item parsers and their failing fixtures

**Files:**
- Create: `crawler/scripts/enrich_competitor_award_specs.py`
- Create: `crawler/tests/test_competitor_award_spec_enrichment.py`

**Step 1: Write failing parser tests**

Use representative ETender `budget_products` JSON-string fixture and Direct
`js_details` fixture. Assert a stable one-line summary includes product name,
quantity when present, and description without raw JSON. Assert malformed or
absent detail returns `None`, never raises.

**Step 2: Run the new test module to verify it fails**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_spec_enrichment.py`

Expected: import or missing-function failure.

**Step 3: Implement minimal pure parsing**

Expose parser functions for ETender and Direct payloads plus a shared text
normalizer. Normalize whitespace, select at most three useful line items, and
cap the final result at 280 characters.

**Step 4: Run the parser tests**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_spec_enrichment.py`

Expected: all parser tests pass.

### Task 2: Fetch only confirmed bounded UZEX candidates

**Files:**
- Modify: `crawler/scripts/enrich_competitor_award_specs.py`
- Modify: `crawler/scripts/run_all_exchange_competitor_monitor.py:40-75`
- Test: `crawler/tests/test_competitor_award_spec_enrichment.py`

**Step 1: Write failing bounded-fetch tests**

Inject a fake HTTP getter. Assert only UZEX rows with `is_win is True`, exact
candidate output and an ID are fetched; assert the limit of 25 is respected;
assert individual HTTP/schema failure leaves its award unchanged and returns a
warning count.

**Step 2: Run to verify the pre-implementation failure**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_spec_enrichment.py`

Expected: failing enrichment/cap assertions.

**Step 3: Implement the isolated enrichment adapter**

Use `GET https://apietender.uzex.uz/api/common/GetTrade/{procedure_id}/0` for
ETender and `GET https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchase/{procedure_id}`
for Direct. Attach `specification_text` only on successful parse. Return a
compact `detail_enrichment` receipt summary (`attempted`, `enriched`,
`failed`, `capped`) without changing source `status` or list `complete`.

**Step 4: Wire after candidate collection**

Call the adapter from the all-exchange runner after `collect_uzex()` returns.
Keep the existing 50-page list collection and all nine source-passport rows
unchanged. Use a 25-card cap per UZEX source.

**Step 5: Run the enrichment test module**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_spec_enrichment.py`

Expected: all parser/fetch/cap/failure-isolation tests pass.

### Task 3: Render detail and track a detail correction

**Files:**
- Modify: `crawler/scripts/monitor_competitor_awards.py:44-63, 119-151`
- Modify: `crawler/tests/test_competitor_award_monitor.py`

**Step 1: Write failing digest and fingerprint tests**

Assert an event with `specification_text` renders one `Позиции:` line capped at
280 characters. Assert an event without it keeps the old digest form. Assert a
specification correction creates a changed event instead of a new award.

**Step 2: Run the monitor tests to verify failure**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py`

Expected: missing line/fingerprint revision assertion.

**Step 3: Implement minimal formatter/fingerprint changes**

Render the optional specification after title and include its normalized stored
value in the event fingerprint. Do not alter qualification gates, keys,
delivery semantics, state threshold, or source passport.

**Step 4: Run the monitor tests**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py`

Expected: all tests pass.

### Task 4: Verify the integrated runner and submit an isolated release

**Files:**
- Modify: `crawler/scripts/enrich_competitor_award_specs.py`
- Modify: `crawler/scripts/run_all_exchange_competitor_monitor.py`
- Modify: `crawler/scripts/monitor_competitor_awards.py`
- Create: `crawler/tests/test_competitor_award_spec_enrichment.py`
- Modify: `crawler/tests/test_competitor_award_monitor.py`
- Create: `docs/superpowers/specs/2026-09-22-competitor-award-spec-details-design.md`
- Create: `docs/plans/2026-09-22-competitor-award-spec-details.md`

**Step 1: Run complete affected checks**

Run the new enrichment module, competitor monitor, award-normalization and
source-manifest standalone suites, then `python3 -m py_compile` on modified
modules and `git diff --check`.

**Step 2: Commit only release files and open a draft PR**

Do not stage the pre-existing untracked `docs/audits/competitor-audit-2026-09-21/`
artifacts.

**Step 3: After separate merge/deploy approval, verify production safely**

Run a bounded live probe of at most one ETender and one Direct detail card,
then replay without `--send-telegram` or `--advance-state`. Do not manually
run the weekly state-advancing job as part of deployment verification.
