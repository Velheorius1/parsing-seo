# Alert Scalar Formatting Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore formatting of scalar `extra_info` values without exposing structured detail payloads.

**Architecture:** Keep mixed metadata in `RawTender.extra_info`. The Telegram formatter remains the only display boundary: structured values are omitted and scalar values are stringified before `_escape_md`.

**Tech Stack:** Python 3.9, Pydantic models, repository-native executable tests.

---

### Task 1: Pin the formatter contract

**Files:**
- Modify: `crawler/tests/test_alert_detail_contract.py`

**Step 1:** Add an assertion that integer, boolean, and `None` scalar values
format without an exception while a `lots` list stays absent.

**Step 2:** Run `PYTHONPATH=. python3 crawler/tests/test_alert_detail_contract.py`.
Expected: fail before the implementation.

### Task 2: Normalize scalars at the display boundary

**Files:**
- Modify: `crawler/core/notifier.py`

**Step 1:** Keep the existing structured-value skip.

**Step 2:** Pass `str(value)` to `_escape_md` for every remaining scalar.

**Step 3:** Re-run the focused test and confirm it passes.

### Task 3: Verify and submit the isolated release

**Files:**
- Modify: `crawler/core/notifier.py`
- Modify: `crawler/tests/test_alert_detail_contract.py`

**Step 1:** Run the focused test plus `test_prequal_detail.py`,
`test_full_crawl_observability.py`, and the notifier routing suite if present.

**Step 2:** Run `git diff --check`; stage only code, test, spec, and plan;
commit as `fix(alerts): Format scalar extra fields`.

**Step 3:** Push a PR. Merge and deploy only under the standing instruction to
complete the agreed C1→C5 queue, then verify the production checkout and the
same focused tests.
