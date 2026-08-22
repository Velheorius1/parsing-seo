"""Tests for crawler.core.zero_result_tracker — task #6 / RISK-1.

Covers:
- classify_outcome precedence (skip > error > empty > with_data)
- advance_source_state transitions (grace, zero streak, recovery, skip-no-auth)
- should_alert / should_send_recovery gating
- track_and_alert end-to-end with mocked session_store + Telegram
"""

import asyncio

import pytest

from crawler.core import zero_result_tracker as zrt


# ─── classify_outcome ──────────────────────────────────────────────────────

class TestClassifyOutcome:
    def test_skip_wins_over_error(self):
        assert zrt.classify_outcome(0, True, "boom") == zrt.SKIPPED_NO_AUTH

    def test_skip_wins_over_count(self):
        # Defensive: if count is non-zero but skipped flag is set, skip wins.
        assert zrt.classify_outcome(5, True, None) == zrt.SKIPPED_NO_AUTH

    def test_error_beats_empty(self):
        assert zrt.classify_outcome(0, False, "http 500") == zrt.ERROR

    def test_error_ignored_if_count(self):
        # Non-zero count + error is a contradiction runner should never produce,
        # but if it does we prefer ERROR (something is wrong).
        assert zrt.classify_outcome(3, False, "http 500") == zrt.ERROR

    def test_with_data(self):
        assert zrt.classify_outcome(1, False, None) == zrt.OK_WITH_DATA

    def test_empty(self):
        assert zrt.classify_outcome(0, False, None) == zrt.OK_EMPTY


# ─── advance_source_state ──────────────────────────────────────────────────

class TestAdvanceState:
    def test_skip_does_not_advance_cycles(self):
        prior = None
        st = zrt.advance_source_state(prior, zrt.SKIPPED_NO_AUTH)
        assert st["cycles_observed"] == 0, "skip must not count as a cycle"
        assert st["consecutive_zeros"] == 0
        assert st["last_outcome"] == zrt.SKIPPED_NO_AUTH

    def test_empty_increments_both(self):
        prior = None
        st = zrt.advance_source_state(prior, zrt.OK_EMPTY)
        assert st["cycles_observed"] == 1
        assert st["consecutive_zeros"] == 1

    def test_error_counts_as_zero(self):
        # ERROR is a form of zero — tracker treats them together.
        st = zrt.advance_source_state(None, zrt.ERROR)
        assert st["consecutive_zeros"] == 1
        assert st["cycles_observed"] == 1

    def test_with_data_resets_streak(self):
        prior = {
            "cycles_observed": 5,
            "consecutive_zeros": 4,
            "last_outcome": zrt.OK_EMPTY,
            "last_observed_at": None,
            "alerted": True,
            "alerted_at": "2026-01-01T00:00:00+00:00",
        }
        st = zrt.advance_source_state(prior, zrt.OK_WITH_DATA)
        assert st["consecutive_zeros"] == 0
        assert st["alerted"] is False
        assert st["alerted_at"] is None
        assert st["cycles_observed"] == 6

    def test_streak_accumulates(self):
        st = None
        for _ in range(5):
            st = zrt.advance_source_state(st, zrt.OK_EMPTY)
        assert st["consecutive_zeros"] == 5


# ─── should_alert / should_send_recovery ───────────────────────────────────

class TestShouldAlert:
    def test_no_alert_during_grace(self):
        """First 3 cycles → no alert even if all zeros."""
        prior = None
        for _ in range(zrt.GRACE_CYCLES - 1):
            p = prior
            prior = zrt.advance_source_state(prior, zrt.OK_EMPTY)
            assert not zrt.should_alert(p, prior), "grace window violated"

    def test_alert_at_threshold(self):
        """Exactly when cycles_observed >= GRACE and consecutive_zeros >= THRESHOLD."""
        st = None
        for _ in range(zrt.ALERT_THRESHOLD - 1):
            st = zrt.advance_source_state(st, zrt.OK_EMPTY)
        prior = st
        st = zrt.advance_source_state(prior, zrt.OK_EMPTY)
        assert zrt.should_alert(prior, st)

    def test_no_repeat_alert(self):
        """Once alerted=True, further zeros do not re-alert."""
        st = None
        for _ in range(zrt.ALERT_THRESHOLD):
            st = zrt.advance_source_state(st, zrt.OK_EMPTY)
        st["alerted"] = True
        prior = st
        st = zrt.advance_source_state(prior, zrt.OK_EMPTY)
        assert not zrt.should_alert(prior, st)

    def test_skip_never_alerts(self):
        st = None
        for _ in range(10):
            st = zrt.advance_source_state(st, zrt.SKIPPED_NO_AUTH)
        prior = st
        st = zrt.advance_source_state(prior, zrt.SKIPPED_NO_AUTH)
        assert not zrt.should_alert(prior, st)


class TestShouldRecover:
    def test_recovery_only_if_previously_alerted(self):
        prior = {
            "cycles_observed": 5, "consecutive_zeros": 0,
            "last_outcome": zrt.OK_EMPTY, "last_observed_at": None,
            "alerted": True, "alerted_at": "2026-01-01T00:00:00+00:00",
        }
        st = zrt.advance_source_state(prior, zrt.OK_WITH_DATA)
        assert zrt.should_send_recovery(prior, st)

    def test_no_recovery_without_prior_alert(self):
        prior = {
            "cycles_observed": 2, "consecutive_zeros": 2,
            "last_outcome": zrt.OK_EMPTY, "last_observed_at": None,
            "alerted": False, "alerted_at": None,
        }
        st = zrt.advance_source_state(prior, zrt.OK_WITH_DATA)
        assert not zrt.should_send_recovery(prior, st)

    def test_no_recovery_without_data(self):
        prior = {
            "cycles_observed": 5, "consecutive_zeros": 3,
            "last_outcome": zrt.OK_EMPTY, "last_observed_at": None,
            "alerted": True, "alerted_at": "2026-01-01T00:00:00+00:00",
        }
        # Another zero, not data → no recovery
        st = zrt.advance_source_state(prior, zrt.OK_EMPTY)
        assert not zrt.should_send_recovery(prior, st)


# ─── track_and_alert (end-to-end with stubs) ───────────────────────────────

class _FakeStore:
    """Stubs session_store.{get_setting,set_setting} for deterministic tests."""
    def __init__(self, initial=None):
        self.saved = None if initial is None else dict(initial)

    def get_setting(self, key):
        return None if self.saved is None else dict(self.saved)

    def set_setting(self, key, value):
        # Snapshot so the caller can't mutate our internal state post-save.
        import json
        self.saved = json.loads(json.dumps(value))
        return True


@pytest.fixture
def fake_store(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(zrt, "session_store", store)
    return store


@pytest.fixture
def capture_tg(monkeypatch):
    sent = []

    async def _fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(zrt, "_send_telegram", _fake_send)
    return sent


@pytest.fixture
def recovery_day(monkeypatch):
    """Считать, что сегодня понедельник — день недельной рассылки recovery.

    Без этого два теста ниже проходили ТОЛЬКО по понедельникам, а остальные
    шесть дней выглядели «предсуществующим падением сьюта» (и именно так их и
    записали 30.07, пока не разобрались). Сам недельный режим закреплён
    отдельным тестом ниже.
    """
    monkeypatch.setattr(zrt, "recovery_is_due", lambda now=None: True)


class TestRecoverySchedule:
    """Недельный режим recovery — правило, а не случайность дня запуска."""

    def test_recovery_only_on_monday(self):
        # Сдвиг через timedelta, а не арифметикой по числу месяца: 27+5 дало
        # «32 июля» и падение теста, который проверяет чужую календарность.
        from datetime import datetime, timedelta, timezone
        monday = datetime(2026, 7, 27, 6, 0, tzinfo=timezone.utc)
        assert monday.weekday() == 0
        assert zrt.recovery_is_due(monday) is True
        for shift in range(1, 7):
            day = monday + timedelta(days=shift)
            assert zrt.recovery_is_due(day) is False, day


class TestTrackAndAlert:
    def _run(self, outcomes, dry_run=False):
        # asyncio.run, а НЕ get_event_loop().run_until_complete: второе берёт
        # ГЛОБАЛЬНЫЙ цикл, а его закрывает любой сосед, вызвавший asyncio.run
        # раньше по алфавиту. Поодиночке файл проходил, в общем прогоне все
        # восемь тестов этого класса падали «There is no current event loop» —
        # и выглядело это как поломка трекера, хотя ломался способ запуска.
        return asyncio.run(zrt.track_and_alert(outcomes, dry_run=dry_run))

    def test_new_source_in_grace_no_alert(self, fake_store, capture_tg):
        out = {"sid-1": {"count": 0, "skipped_no_auth": False, "error": None}}
        sent = self._run(out)
        assert sent == 0
        assert capture_tg == []
        assert fake_store.saved["sources"]["sid-1"]["cycles_observed"] == 1

    def test_three_consecutive_zeros_alerts(self, fake_store, capture_tg, recovery_day):
        out = {"sid-x": {"count": 0, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(out)
        assert len(capture_tg) == 1, "exactly one alert after crossing threshold"
        assert "sid-x" in capture_tg[0]
        assert fake_store.saved["sources"]["sid-x"]["alerted"] is True

    def test_alert_suppressed_on_repeat(self, fake_store, capture_tg, recovery_day):
        out = {"sid-x": {"count": 0, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD + 3):
            self._run(out)
        assert len(capture_tg) == 1, "still exactly one alert"

    def test_recovery_clears_state_and_messages(self, fake_store, capture_tg, recovery_day):
        out_zero = {"sid-x": {"count": 0, "skipped_no_auth": False, "error": None}}
        out_ok = {"sid-x": {"count": 7, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(out_zero)
        assert len(capture_tg) == 1
        # Recovery
        self._run(out_ok)
        assert len(capture_tg) == 2, "разные прогоны — разные сводки"
        assert "снова" in capture_tg[1].lower() or "recovery" in capture_tg[1].lower()
        st = fake_store.saved["sources"]["sid-x"]
        assert st["alerted"] is False
        assert st["consecutive_zeros"] == 0

    def test_skipped_no_auth_does_not_trip_alert(self, fake_store, capture_tg):
        out = {"sid-x": {"count": 0, "skipped_no_auth": True, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD * 3):
            self._run(out)
        assert capture_tg == []
        # Cycles_observed stayed 0 since every cycle was skipped.
        assert fake_store.saved["sources"]["sid-x"]["cycles_observed"] == 0

    def test_error_outcome_counts_as_zero(self, fake_store, capture_tg, recovery_day):
        out = {"sid-y": {"count": 0, "skipped_no_auth": False, "error": "http 500"}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(out)
        assert len(capture_tg) == 1
        # Alert body mentions "ошибка"
        assert "http 500" in capture_tg[0] or "ошибк" in capture_tg[0].lower()

    def test_dry_run_does_not_persist_or_send(self, fake_store, capture_tg):
        out = {"sid-x": {"count": 0, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(out, dry_run=True)
        assert capture_tg == []
        # State never written
        assert fake_store.saved is None

    def test_mixed_outcomes_single_run(self, fake_store, capture_tg, recovery_day):
        # Seed pre-existing streaks so one source crosses the threshold on this run.
        import json
        fake_store.saved = {
            "version": zrt.STATE_VERSION,
            "sources": {
                "sid-a": {
                    "cycles_observed": 5, "consecutive_zeros": 2,
                    "last_outcome": zrt.OK_EMPTY, "last_observed_at": None,
                    "alerted": False, "alerted_at": None,
                },
                "sid-b": {
                    "cycles_observed": 5, "consecutive_zeros": 3,
                    "last_outcome": zrt.OK_EMPTY, "last_observed_at": None,
                    "alerted": True, "alerted_at": "2026-01-01T00:00:00+00:00",
                },
            },
        }
        # round-trip to simulate persisted JSON
        fake_store.saved = json.loads(json.dumps(fake_store.saved))

        out = {
            "sid-a": {"count": 0, "skipped_no_auth": False, "error": None},  # 3rd zero → alert
            "sid-b": {"count": 10, "skipped_no_auth": False, "error": None},  # recovery
            "sid-c": {"count": 0, "skipped_no_auth": True, "error": None},   # skip — ignored
        }
        self._run(out)
        # 22.08: доставка стала недельной и ОДНИМ сообщением. Раньше здесь было
        # два отдельных — по сообщению на источник; ровно из-за этого канал
        # получал по 1-5 «молчит» каждые два часа. Содержимое не потеряно:
        # обе новости обязаны быть в одной сводке. sid-c пропущен (нет токена).
        assert len(capture_tg) == 1, "недельная сводка — одно сообщение"
        texts = capture_tg[0]
        assert "sid-a" in texts
        assert "sid-b" in texts
        assert "sid-c" not in texts
        assert "sid-c" not in texts
