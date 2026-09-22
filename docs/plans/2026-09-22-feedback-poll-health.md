# Feedback Polling Health Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make healthcheck report whether the feedback bot's Telegram long-polling is actually healthy.

**Architecture:** Keep unit and stale-code status in `feedback_bot`. Add an independent
`feedback_bot.polling` result sourced from a bounded read of the service journal. The
healthcheck never calls Telegram `getUpdates`, preventing self-inflicted polling conflicts.

**Tech Stack:** Python 3.9, subprocess, systemd journalctl, pytest.

---

### Task 1: Add polling-health regression tests

**Files:**
- Modify: `crawler/tests/test_deploy_fresh.py`
- Modify: `crawler/scripts/healthcheck.py`

**Step 1: Write the failing tests**

Add mocked-journal tests that instantiate `HealthCheck` and assert:

```python
# a current 409 produces feedback_bot.polling FAIL
# a current getUpdates HTTP 200 produces feedback_bot.polling OK
# no current poll evidence produces feedback_bot.polling WARN
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest crawler/tests/test_deploy_fresh.py -q`

Expected: new tests fail because the component does not exist.

**Step 3: Implement the minimal journal observer**

Read the last five minutes through bounded `journalctl` only after the systemd unit
is confirmed active. Add the separate component without changing the existing
service/stale-code component.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest crawler/tests/test_deploy_fresh.py -q`

Expected: all tests pass.

**Step 5: Run adjacent regression checks**

Run: `.venv/bin/python -m pytest crawler/tests/test_outcome_buttons.py crawler/tests/test_digest_feedback.py crawler/tests/test_deploy_fresh.py -q`

Expected: all tests pass.

**Step 6: Commit**

Commit only `crawler/scripts/healthcheck.py`, its tests, and this plan. Do not add
the pre-existing untracked competitor-audit artifacts.
