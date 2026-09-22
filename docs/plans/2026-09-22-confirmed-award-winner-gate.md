# Confirmed Award Winner Gate Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent UZEX/ETender rows without an explicit confirmed win from entering the weekly competitor-award state or digest.

**Architecture:** Keep the source-neutral weekly monitor and its existing exact-INN, amount, currency, and contract gates. Add one UZEX/ETender-specific truth gate at the collector-output boundary: only `is_win is True` may become an event. Preserve the raw row shape and final-total/evidence normalization.

**Tech Stack:** Python 3.9, standalone assertion test modules, JSON receipts.

---

### Task 1: Specify confirmed UZEX award fixtures

**Files:**
- Modify: `crawler/tests/test_competitor_award_monitor.py`

**Step 1: Write the failing tests**

Add an ETender-shaped row with a valid exact INN, `final_total`, UZS currency,
and `is_win=False`; assert `_qualified_source_awards()` returns no awards.
Add the otherwise identical `is_win=True` row; assert it returns one normalized
award. Mark all pre-existing UZEX/ETender winner fixtures as `is_win=True` so
they state their intended evidence contract explicitly.

**Step 2: Run the focused test module to verify it fails**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py`

Expected: the explicit false-win test fails before the production gate exists.

### Task 2: Gate UZEX/ETender award rows at the monitor boundary

**Files:**
- Modify: `crawler/scripts/monitor_competitor_awards.py:110-143`
- Test: `crawler/tests/test_competitor_award_monitor.py`

**Step 1: Implement the minimal guard**

Before normalizing a UZEX direct or ETender deals row into an event, skip it
unless `row.get("is_win") is True`. Do not modify other source adapters or the
source passport. Include `is_win` in the event fingerprint so a confirmed row's
evidence participates in its persisted representation.

**Step 2: Run the focused test module**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py`

Expected: all assertions pass.

### Task 3: Verify compatibility and receipt replay

**Files:**
- Test: `crawler/tests/test_competitor_audit_awards.py`
- Verify (read-only): production ETender receipt at
  `/opt/parsing-seo/data/competitor-award-monitor/receipts/`

**Step 1: Run the related audit and syntax checks**

Run: `PYTHONPATH=. python3 crawler/tests/test_competitor_audit_awards.py` and
`python3 -m py_compile crawler/scripts/monitor_competitor_awards.py`.

**Step 2: Replay the saved receipt without delivery or state advance**

After deployment, call the monitor's pure qualification function against the
latest saved ETender receipt. Assert exactly three qualifying PECHATNIK VOSTOKA
rows and confirm no Telegram or state-writing flag is used.

### Task 4: Commit and submit the isolated change

**Files:**
- Modify: `crawler/scripts/monitor_competitor_awards.py`
- Modify: `crawler/tests/test_competitor_award_monitor.py`
- Create: `docs/superpowers/specs/2026-09-22-confirmed-award-winner-gate-design.md`
- Create: `docs/plans/2026-09-22-confirmed-award-winner-gate.md`

**Step 1: Commit only the release files**

Do not stage the pre-existing untracked competitor-audit artifacts.

**Step 2: Open a pull request**

Do not merge, deploy, run the state-advancing job, or send Telegram without the
separate production decision.
