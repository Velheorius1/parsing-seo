# C1–C5 Reliability Fixes Implementation Plan

> **For Codex:** Execute this plan task-by-task with isolated verification and commits.

**Goal:** Close the reproduced C2–C5 integration gaps without expanding alert scope or replacing the existing monitor architecture.

**Architecture:** Four releases preserve existing boundaries. C2 makes database lookup uncertainty explicit and gives fresh detail priority. C5 adds a file-backed durable delivery outbox. C3 validates Ebirja pagination before state can advance. C4 represents unresolved identity as a non-destructive partial source result.

**Tech Stack:** Python 3.9, existing standalone offline test scripts, Supabase JSONB, atomic local JSON files, GitHub PRs, VPS cron.

---

### Task 1: C2 — preserve fresh and legacy tender detail safely

**Files:**

- Modify: `crawler/core/db.py:175-305`
- Modify: `crawler/tests/test_detail_text_persistence.py`

**Step 1:** Add three regressions: fresh `_detail_text` beats stored detail; legacy stored `search_text` is conservatively migrated; a failed existence lookup is neither written nor returned as new.

**Step 2:** Run `PYTHONPATH=. python3 crawler/tests/test_detail_text_persistence.py`; observe failures on current implementation.

**Step 3:** Return known rows plus unknown lookup keys; remove unknown keys before restore/newness/write. Restore old detail only if the incoming tender lacks it; give a non-empty incoming `lots` payload the same priority. Read stored `search_text` for opted-in rows and migrate only the removable residual to `_detail_text`.

**Step 4:** Run detail tests, alert contract tests, prequalification tests and `python3 -m compileall crawler/core/db.py`.

**Step 5:** Commit only C2 code and tests.

### Task 2: C5 — durable delivery and retry

**Files:**

- Modify: `crawler/scripts/monitor_competitor_awards.py:1-320`
- Modify: `scripts/run_competitor_award_weekly.sh:1-21`
- Modify: `crawler/tests/test_competitor_award_monitor.py`

**Step 1:** Add regressions for a transport exception after batch one, retrying pending events absent from the next source snapshot, and no outbox mutation for preview/bootstrap.

**Step 2:** Run `PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py`; observe failures.

**Step 3:** Add an atomically persisted outbox, merge it into delivery candidates, catch sender exceptions, checkpoint each confirmed batch in state and outbox, and wire the stable outbox path in weekly cron. Preserve existing state-file compatibility.

**Step 4:** Run competitor monitor tests, crawl-completeness tests, `python3 -m compileall crawler/scripts`, and the original C5 probe.

**Step 5:** Commit only C5 code, cron wiring and tests.

### Task 3: C3 — reject contradictory empty pagination

**Files:**

- Modify: `crawler/scripts/collect_ebirja_contract_api.py:65-120`
- Modify: `crawler/tests/test_competitor_crawl_completeness.py`

**Step 1:** Add a failing response with empty data, `pageCount=10`, `totalCount=100`; assert incomplete and preserved source baseline.

**Step 2:** Validate/normalize pagination metadata before treating an empty page as authoritative. Keep genuinely empty page-zero responses complete only with consistent zero metadata.

**Step 3:** Run crawl-completeness and competitor-monitor tests plus compileall.

**Step 4:** Commit only C3 code and test.

### Task 4: C4 — distinguish foreign and unresolved winner identity

**Files:**

- Modify: `crawler/scripts/run_all_exchange_competitor_monitor.py:25-67`
- Modify: `crawler/scripts/monitor_competitor_awards.py:225-262`
- Modify: `crawler/tests/test_ebirja_winner_identity.py`
- Modify: `crawler/tests/test_competitor_award_monitor.py`

**Step 1:** Add regressions: valid foreign INN remains a complete exclusion; missing/invalid INN produces partial identity, preserves prior state and still emits verified new awards.

**Step 2:** Return confirmed/rejected/unresolved identity counts. Convert unresolved identity to `partial_identity`; in delta construction union confirmed current awards with prior state, mark it not fully reported, and never remove old keys because identity is unknown.

**Step 3:** Run identity, monitor, completeness and all C1–C5 contract scripts; run compileall.

**Step 4:** Commit only C4 code and tests.

### Task 5: Integration, delivery and release verification

**Files:**

- Modify: `docs/audits/2026-09-22-c1-c5-integration-review.md` only if a factual status needs updating.

**Step 1:** Run the seven original mocked probes against final code. Their old failure assertions must be replaced with final success assertions or equivalent targeted tests; no stale diagnostic test may be reported as a pass.

**Step 2:** Run the complete offline C1–C5 suite from a clean checkout and inspect the diff against `main`.

**Step 3:** Push each release as a separate PR, merge sequentially, deploy the individual release to `/opt/parsing-seo`, and repeat the affected offline tests on VPS. Do not run a live full crawl or send a live Telegram report as a test.

**Step 4:** Update the audit/context only with verified results and report any remaining external limitations separately.
