# STANDARD POLIGRAF BOOKS INN Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the confirmed STANDARD POLIGRAF BOOKS INN to the competitor registry without changing any other identity, alert, or collection rule.

**Architecture:** The versioned JSON registry remains the single identity input for all collectors.  `competitor_audit` validates and exposes only exact nine-digit INNs; the test asserts that Books resolves by its own INN while Service remains independent.

**Tech Stack:** Python 3.9, JSON registry, repository-native executable test modules.

---

### Task 1: Encode the identity boundary in a failing test

**Files:**
- Modify: `crawler/tests/test_competitor_audit.py`

**Step 1: Write the failing test**

Replace the unresolved Books assertion with one that loads the registry and
asserts:

```python
assert books["inn"] == "305970088"
assert entity_for_inn(registry, "305970088")["name"] == "STANDARD POLIGRAF BOOKS"
assert entity_for_inn(registry, "207063624")["name"] == "STANDARD POLIGRAF SERVICE"
assert books["inn"] != entity_for_inn(registry, "207063624")["inn"]
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_audit.py`

Expected: the Books assertion fails while the registry still contains `null`.

**Step 3: Commit**

Do not commit the deliberately failing test independently; follow immediately
with the minimal registry update so every commit stays runnable.

### Task 2: Add the confirmed Books record

**Files:**
- Modify: `crawler/config/competitor_registry.json`

**Step 1: Make the minimal registry update**

For `STANDARD POLIGRAF BOOKS`:

- set `inn` to string `305970088`;
- retain existing user spellings and add `standard poligraph books` and
  `standard polygraph books` as search aliases;
- set status to `registry_catalog_crosscheck`;
- cite Statsnet, GoldenPages, and Sprav;
- state explicitly that a shared address with Service never implies a shared INN.

Do not alter `STANDARD POLIGRAF SERVICE`, `CENTRIS`, `CENTRIS-PRINT`, or any
other registry row.

**Step 2: Run focused test**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_audit.py`

Expected: all registry audit tests pass.

### Task 3: Run regression verification and commit

**Files:**
- Modify: `crawler/config/competitor_registry.json`
- Modify: `crawler/tests/test_competitor_audit.py`

**Step 1: Run the affected suite**

Run:

```bash
PYTHONPATH=. python3 crawler/tests/test_competitor_audit.py
PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py
PYTHONPATH=. python3 crawler/tests/test_competitor_award_spec_enrichment.py
PYTHONPATH=. python3 crawler/tests/test_competitor_audit_awards.py
PYTHONPATH=. python3 crawler/tests/test_competitor_source_manifest.py
```

Expected: every test passes.

**Step 2: Verify scope**

Run `git diff --check` and `git diff --stat origin/main...HEAD`; ensure only
the registry, its test, and the already-approved spec/plan are tracked.

**Step 3: Commit**

```bash
git add crawler/config/competitor_registry.json crawler/tests/test_competitor_audit.py \
  docs/superpowers/specs/2026-09-22-standard-poligraf-books-inn-design.md \
  docs/plans/2026-09-22-standard-poligraf-books-inn.md
git commit -m "fix(competitors): Add Books exact INN"
```

The commit must contain no audit artefacts and retain a clean tracked tree.

### Task 4: Submit release for review

**Files:**
- No code changes

**Step 1: Push the isolated branch and create a PR**

Describe the identity evidence, the non-merge guardrail, and the full test
results.  Do not merge or deploy until separately approved.
