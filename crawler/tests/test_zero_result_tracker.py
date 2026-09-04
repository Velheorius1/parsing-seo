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
        # Recovery тем же днём: с недельным маркером (24.08, третья редакция)
        # вторая сводка в ту же неделю НЕ уходит — восстановление ждёт в
        # pending до следующего понедельника, как любой будничный переход.
        self._run(out_ok)
        assert len(capture_tg) == 1, "одна неделя — одна сводка"
        st = fake_store.saved["sources"]["sid-x"]
        assert st["alerted"] is False
        assert st["consecutive_zeros"] == 0
        assert "снова" in st.get("pending_recovery", "").lower(), "восстановление обязано ждать в pending"

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

    def test_alert_crossing_on_tuesday_is_delivered_on_monday(self, fake_store, capture_tg, monkeypatch):
        """ГЛАВНЫЙ регрессионный тест недельной сводки (22.08, вторая редакция).

        Первая редакция гейта отправляла сообщение, собранное на прогоне
        пересечения порога, и только если «сегодня понедельник». Но should_alert
        срабатывает ровно один раз и ставит alerted=True — в понедельник он уже
        молчит. Источник, замолчавший во вторник, НЕ ПОПАДАЛ ни в одну сводку.
        Старые тесты этого не видели: фикстура делала каждый день понедельником.
        """
        due = {"v": False}
        monkeypatch.setattr(zrt, "recovery_is_due", lambda now=None: due["v"])
        out = {"sid-t": {"count": 0, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):          # вторник: порог пройден
            self._run(out)
        assert capture_tg == [], "в будни ничего не шлём"
        assert fake_store.saved["sources"]["sid-t"].get("pending_alert"), "тревога не запомнена"

        due["v"] = True                                 # понедельник
        self._run(out)
        assert len(capture_tg) == 1, "в понедельник сводка обязана уйти"
        assert "sid-t" in capture_tg[0]
        assert not fake_store.saved["sources"]["sid-t"].get("pending_alert"), "флаг не снят"

        self._run(out)                                  # второй прогон того же дня
        assert len(capture_tg) == 1, "повтора в тот же день быть не должно"

    def test_recovery_on_thursday_is_delivered_on_monday(self, fake_store, capture_tg, monkeypatch):
        """Та же дыра у «снова с данными» — и она стояла с 30.06."""
        due = {"v": False}
        monkeypatch.setattr(zrt, "recovery_is_due", lambda now=None: due["v"])
        zero = {"sid-r": {"count": 0, "skipped_no_auth": False, "error": None}}
        ok = {"sid-r": {"count": 9, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(zero)
        self._run(ok)                                   # четверг: восстановился
        assert capture_tg == []
        st = fake_store.saved["sources"]["sid-r"]
        assert st.get("pending_recovery") and not st.get("pending_alert")

        due["v"] = True
        self._run(ok)
        assert len(capture_tg) == 1
        assert "снова" in capture_tg[0].lower() and "sid-r" in capture_tg[0]
        assert "не успела уйти" in capture_tg[0], "про недоставленную тревогу надо сказать прямо"

    def test_second_monday_crawl_does_not_send_a_second_digest(self, fake_store, capture_tg, recovery_day):
        """Первый живой понедельник (24.08): три мини-сводки за ночь — каждый
        краул с новыми переходами слал свою. Одна неделя = одна сводка;
        переход понедельничного дня ждёт следующего понедельника, как и
        вторничный."""
        a = {"sid-a": {"count": 0, "skipped_no_auth": False, "error": None}}
        b = {"sid-b": {"count": 0, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(a)
        assert len(capture_tg) == 1, "первая сводка недели уходит"
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(b)                      # новый источник пересёк порог тем же днём
        assert len(capture_tg) == 1, "вторая сводка в ту же неделю — запрещена"
        st = fake_store.saved["sources"]["sid-b"]
        assert st.get("pending_alert"), "невысланное обязано остаться в pending"

    def test_next_week_sends_the_leftover_pending(self, fake_store, capture_tg, recovery_day, monkeypatch):
        a = {"sid-a": {"count": 0, "skipped_no_auth": False, "error": None}}
        b = {"sid-b": {"count": 0, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(a)
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(b)
        assert len(capture_tg) == 1
        # неделя прошла: метка «в прошлом» — сдвигаем сравнение недель
        from datetime import datetime, timedelta, timezone
        future = datetime.now(timezone.utc) + timedelta(days=7)

        class _FD(datetime):
            @classmethod
            def now(cls, tz=None):
                return future if tz else future.replace(tzinfo=None)

        monkeypatch.setattr(zrt, "datetime", _FD)
        self._run(b)
        assert len(capture_tg) == 2, "следующий понедельник добирает остаток"
        assert "sid-b" in capture_tg[1]

    def test_broken_marker_means_not_sent(self):
        """Сломанная метка = «не уходила»: редкий дубль лучше потерянной недели."""
        assert zrt._digest_sent_this_week({"last_weekly_digest_at": "мусор"}) is False
        assert zrt._digest_sent_this_week({}) is False
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        assert zrt._digest_sent_this_week({"last_weekly_digest_at": now_iso}) is True

    def test_failed_send_keeps_pending_for_next_run(self, fake_store, monkeypatch, recovery_day):
        """Отказ Telegram не должен терять сводку: флаги остаются до удачи."""
        calls = []

        async def _fail(text):
            calls.append(text)
            return False

        monkeypatch.setattr(zrt, "_send_telegram", _fail)
        out = {"sid-f": {"count": 0, "skipped_no_auth": False, "error": None}}
        for _ in range(zrt.ALERT_THRESHOLD):
            self._run(out)
        assert calls, "попытка отправки была"
        assert fake_store.saved["sources"]["sid-f"].get("pending_alert"), "после отказа флаг обязан остаться"

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


class TestStandingSilences:
    """«Молчат давно» — источник виден каждый понедельник, пока молчит.

    Урок etender (19-31.08): сводка из одних переходов теряет источник после
    первой тревоги — alerted=True, нового перехода нет, и 12 дней тишины не
    всплыли ни в одной сводке. Секция строится из состояния, а не из переходов.
    """

    def _silent(self, zeros=10, outcome=None, pending=None, alerted_at="2026-08-19T06:00:00+00:00"):
        st = {
            "cycles_observed": zeros + 5,
            "consecutive_zeros": zeros,
            "last_outcome": outcome or zrt.OK_EMPTY,
            "last_observed_at": "2026-09-01T00:00:00+00:00",
            "alerted": True,
            "alerted_at": alerted_at,
        }
        if pending:
            st["pending_alert"] = pending
        return st

    def test_lists_long_silent_and_skips_fresh_and_recovered(self):
        state = {
            "old-silent": self._silent(zeros=74),
            "erroring": self._silent(zeros=20, outcome=zrt.ERROR),
            # свежее пересечение этой недели — уже в секции «Молчат», не дублировать
            "fresh": self._silent(zeros=3, pending="fresh молчит 3 циклов"),
            # ожил — не молчит
            "revived": {"alerted": False, "last_outcome": zrt.OK_WITH_DATA,
                        "consecutive_zeros": 0, "cycles_observed": 50},
        }
        lines = zrt.standing_report(state)["rows"]
        assert len(lines) == 2
        # сортировка по глубине молчания, дата из alerted_at
        assert lines[0].startswith("old-silent — 74 циклов")
        assert "с 19.08" in lines[0]
        assert lines[1].startswith("erroring — 20 циклов")
        assert not any("fresh" in l or "revived" in l for l in lines)

    def test_broken_alerted_at_degrades_to_cycles_only(self):
        state = {"x": self._silent(zeros=7, alerted_at="мусор")}
        lines = zrt.standing_report(state)["rows"]
        assert lines == ["x — 7 циклов"]

    def test_digest_has_standing_section_and_cap(self):
        standing = ["s%02d — %d циклов" % (i, 100 - i) for i in range(15)]
        out = zrt._weekly_digest([], [], standing)
        assert "Молчат давно (15):" in out
        assert "s11" in out and "s12" not in out, "кап 12 строк"
        assert "и ещё 3" in out
        # секций переходов нет — и заголовков их нет
        assert "Молчат (" not in out.replace("Молчат давно (", "")
        assert "Снова с данными" not in out

    def test_monday_digest_fires_on_standing_alone(self, fake_store, capture_tg, recovery_day):
        # неделя без новых переходов, но с давно молчащим источником
        fake_store.saved = {"version": zrt.STATE_VERSION, "sources": {
            "etender": self._silent(zeros=74),
        }}
        asyncio.run(
            zrt.track_and_alert({"alive": {"count": 5, "skipped_no_auth": False, "error": None}})
        )
        assert len(capture_tg) == 1
        assert "Молчат давно (1):" in capture_tg[0]
        assert "etender — 74 циклов, с 19.08" in capture_tg[0]
        # маркер недели встал — второй краул того же понедельника молчит
        asyncio.run(
            zrt.track_and_alert({"alive": {"count": 5, "skipped_no_auth": False, "error": None}})
        )
        assert len(capture_tg) == 1, "standing не должен пробивать недельный маркер"


class TestDigestLimit:
    """Кап 4096 (05.09). Telegram отдаёт 400 на длинный текст, а неудачная
    отправка ОСТАВЛЯЕТ pending-флаги: сводка, однажды переросшая лимит, не ушла
    бы уже никогда. В состоянии 101 источник — сценарий достижим.
    """

    def _mass(self, n):
        return ["tg-uzbekneftegaz-%03d снова с данными (count=1) — молчал, тревога не успела уйти" % i
                for i in range(n)]

    def test_mass_recovery_week_fits_in_one_message(self):
        # 101 источник × ~75 символов = ~7,5k без капа
        out = zrt._weekly_digest(self._mass(20), self._mass(101), self._mass(40))
        assert len(out) <= zrt.TG_TEXT_LIMIT, "сводка не влезает — TG вернёт 400"

    def test_counts_stay_full_when_truncated(self):
        """Усечение должно быть видно, а не выглядеть «источников стало меньше»."""
        out = zrt._weekly_digest(self._mass(20), self._mass(101), self._mass(40))
        assert "Снова с данными (101):" in out, "счётчик обрезан вместе со строками"
        assert "и ещё" in out

    def _section_rows(self, out, title):
        """Сколько ЖИВЫХ строк (не счётчик «и ещё») показано в секции."""
        head = out.index("%s (" % title)
        rest = out[out.index("\n", head) + 1:]
        rows = 0
        for line in rest.split("\n"):
            if not line.startswith("\u00b7 "):
                break
            if "и ещё" not in line:
                rows += 1
        return rows

    def test_every_section_keeps_live_rows_when_overflowing(self):
        """Место делится, а не отдаётся первой секции целиком.

        Первая редакция капа резала от наименее срочной секции вниз: на живом
        состоянии прода (101 источник) она показывала 77 тревог и НОЛЬ
        восстановлений с давними молчунами, недобрав 892 символа бюджета.
        """
        out = zrt._weekly_digest(self._mass(101), self._mass(101), self._mass(40))
        assert len(out) <= zrt.TG_TEXT_LIMIT
        for title in ("Молчат", "Снова с данными", "Молчат давно"):
            assert self._section_rows(out, title) >= 1, "секция %s схлопнута в ноль" % title

    def test_budget_is_actually_used(self):
        """Недобор бюджета = молча потерянная информация."""
        out = zrt._weekly_digest(self._mass(101), self._mass(101), self._mass(40))
        longest = max(len(x) for x in self._mass(101)) + 4
        assert zrt.TG_TEXT_LIMIT - len(out) < longest, "влезла бы ещё строка, а её выкинули"

    def test_pathological_single_line_degrades_to_a_counter(self):
        """Строка длиннее лимита не рвёт сообщение: секция схлопывается в счётчик.

        Пин фактического поведения, а не задуманного: первая редакция теста
        ждала жёсткого обреза с многоточием, но кап секции опускается до нуля
        раньше, и текст остаётся целым. Обрез по символам в `_weekly_digest`
        при нынешних заголовке и футере недостижим — оставлен поясом.
        """
        out = zrt._weekly_digest(["ы" * 9000], [], [])
        assert len(out) <= zrt.TG_TEXT_LIMIT
        assert "Молчат (1):" in out and "и ещё 1" in out
        assert "ыыыы" not in out
        assert out.rstrip().endswith("healthcheck (06:00).")

    def test_normal_digest_unchanged_by_the_cap(self):
        """Обычная неделя рендерится как раньше — кап не должен ничего трогать."""
        out = zrt._weekly_digest(["a молчит 3 циклов"], ["b снова с данными"], ["c — 700 циклов"])
        assert "Молчат (1):" in out and "\u00b7 a молчит 3 циклов" in out
        assert "Снова с данными (1):" in out and "\u00b7 b снова с данными" in out
        assert "Молчат давно (1):" in out and "\u00b7 c — 700 циклов" in out
        assert "и ещё" not in out
        assert out.startswith("\U0001f4e1 Источники за неделю")
        assert out.rstrip().endswith("healthcheck (06:00).")


class TestReconcileState:
    """Сверка состояния с конфигом (05.09).

    Состояние не чистилось никогда: на 101 запись приходилось 10 зомби
    (`cooperation-*` эпохи, когда лоты ходили через runner, последнее
    наблюдение 27.04) и 12 выключенных руками. Секция «молчат давно»
    показывала 6 таких из 21 строки.
    """

    def _known(self, extra=()):
        # конфиг заведомо длиннее MIN_KNOWN_IDS, иначе сработает защита
        ids = {"src-%03d" % i for i in range(60)}
        ids.update(extra)
        return ids

    def _silent(self, zeros=10, pending=None):
        st = {"cycles_observed": zeros + 5, "consecutive_zeros": zeros,
              "last_outcome": zrt.OK_EMPTY, "alerted": True,
              "alerted_at": "2026-04-27T22:03:00+00:00"}
        if pending:
            st["pending_alert"] = pending
        return st

    def test_zombie_is_dropped_disabled_is_kept(self):
        state = {"live": self._silent(), "disabled": self._silent(), "zombie": self._silent()}
        known = self._known({"live", "disabled"})
        rep = zrt.reconcile_state(state, active_ids={"live"}, known_ids=known)
        assert rep["pruned"] == ["zombie"]
        assert "zombie" not in state
        assert "disabled" in state, "выключенный источник вернут — историю не теряем"

    def test_stale_flags_are_cleared_from_disabled(self):
        state = {"disabled": self._silent(pending="disabled молчит 3 циклов")}
        rep = zrt.reconcile_state(state, active_ids=set(), known_ids=self._known({"disabled"}))
        assert rep["disabled_cleared"] == ["disabled"]
        assert "pending_alert" not in state["disabled"], "тревога апреля всплыла бы через полгода"

    def test_short_known_ids_touches_nothing(self):
        """Сбой чтения yaml не должен сносить состояние."""
        state = {"a": self._silent(), "b": self._silent(pending="x")}
        before = {k: dict(v) for k, v in state.items()}
        for broken in (None, set(), {"a"}):
            rep = zrt.reconcile_state(state, active_ids={"a"}, known_ids=broken)
            assert rep == {"pruned": [], "disabled_cleared": []}
        assert state == before, "состояние тронуто при подозрительном конфиге"

    def test_standing_skips_disabled_sources(self):
        state = {"live": self._silent(zeros=700), "off": self._silent(zeros=900)}
        rows = zrt.standing_report(state, active_ids={"live"})["rows"]
        assert len(rows) == 1 and rows[0].startswith("live —")
        # без active_ids поведение прежнее — обратная совместимость
        assert len(zrt.standing_report(state)["rows"]) == 2

    def test_digest_ignores_disabled_pending(self, fake_store, capture_tg, recovery_day):
        fake_store.saved = {"version": zrt.STATE_VERSION, "sources": {
            "off": self._silent(pending="off молчит 3 циклов"),
            "live": self._silent(zeros=700),
        }}
        asyncio.run(zrt.track_and_alert(
            {"live": {"count": 0, "skipped_no_auth": False, "error": None}},
            active_ids={"live"}, known_ids=self._known({"live", "off"})))
        assert len(capture_tg) == 1
        assert "off" not in capture_tg[0], "выключенный источник попал в сводку"
        assert "live" in capture_tg[0]


class TestExcusedSilence:
    """Общий реестр объяснённого молчания (05.09).

    Первая живая сводка потребовала действия по семи источникам, решение по
    которым принято месяцы назад: healthcheck уже писал «7 молчат с
    объяснением», а трекер про этот список не знал.
    """

    def _silent(self, zeros):
        return {"cycles_observed": zeros + 5, "consecutive_zeros": zeros,
                "last_outcome": zrt.OK_EMPTY, "alerted": True,
                "alerted_at": "2026-04-21T00:00:00+00:00"}

    def test_excused_become_a_counter_not_rows(self):
        state = {"real": self._silent(100), "ungm": self._silent(1300),
                 "tenderweek": self._silent(780)}
        rep = zrt.standing_report(state, excused_ids={"ungm", "tenderweek"})
        assert rep["rows"] == ["real — 100 циклов, с 21.04"], rep
        assert rep["excused"] == 2
        # служебная строка вне счётчика заголовка: «Молчат давно (1)», не (2)
        out = zrt._weekly_digest([], [], rep["rows"], rep["excused"])
        assert "Молчат давно (1):" in out
        assert "+ 2 молчат по известной причине (реестр healthcheck)" in out

    def test_counter_alone_does_not_justify_a_digest(self):
        """Сводка ради «6 молчат по известной причине» — шум."""
        state = {"ungm": self._silent(1300), "tenderweek": self._silent(780)}
        rep = zrt.standing_report(state, excused_ids={"ungm", "tenderweek"})
        assert rep["rows"] == [] and rep["excused"] == 2
        # довесок не рисуется без настоящих молчунов
        assert "по известной причине" not in zrt._weekly_digest([], [], rep["rows"], rep["excused"])

    def test_registry_maps_names_to_ids_on_the_real_config(self):
        """Whitelist ведётся по именам, трекер работает по id — перевод обязан
        совпадать с живым конфигом, иначе реестр молча не действует."""
        import os
        from crawler.core.source_health import excused_source_ids, DEAD_SOURCES_WHITELIST
        cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(zrt.__file__))),
                           "config", "sources.yaml")
        ids = excused_source_ids(cfg)
        assert len(ids) >= 15, "перевод имён в id перестал находить источники"
        assert {"ungm", "hamkorbank", "ipoteka-bank"} <= ids
        assert len(DEAD_SOURCES_WHITELIST) >= 30

    def test_broken_config_path_is_not_fatal(self):
        from crawler.core.source_health import excused_source_ids
        assert excused_source_ids("/nope/does-not-exist.yaml") == set()
