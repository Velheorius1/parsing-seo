"""Лиды из Telegram: срок без прошлого года (аудит 21.10.2026).

ЧТО БЫЛО. В промпт разбора полей не передавалась текущая дата, и год, которого
в тексте нет («к 1 октября»), модель брала из своих данных — прошлый.
«Футболки 200 шт к 1 октября» записаны со сроком 01.10.2025, «Брендированные
мини-плитки шоколада 75 шт» от 02.10.2026 — со сроком 15.04.2025; оба лида
отброшены как просроченные.

Здесь держится: в промпт идёт день сообщения по Ташкенту; срок раньше дня
сообщения — ошибка разбора: тот же день и месяц в ближайшие полгода, иначе срока
нет (лид не теряется); настоящий будущий срок не трогается.

Run: .venv/bin/python3 -m pytest crawler/tests/test_tg_lead_deadline_year.py -q
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters import telegram_adapter as T  # noqa: E402

DAY = date(2026, 9, 28)


def test_last_year_with_upcoming_day_moves_to_this_year():
    assert T._fix_lead_deadline("01.10.2025", DAY) == "01.10.2026"


def test_hopeless_past_date_is_dropped_not_expired():
    assert T._fix_lead_deadline("15.04.2025", date(2026, 10, 2)) is None


def test_real_future_deadline_untouched():
    assert T._fix_lead_deadline("05.10.2026", DAY) == "05.10.2026"
    assert T._fix_lead_deadline("28.09.2026", DAY) == "28.09.2026"  # сегодня


def test_new_year_rollover():
    assert T._fix_lead_deadline("10.01.2026", date(2026, 12, 20)) == "10.01.2027"


def test_non_date_passes_through():
    assert T._fix_lead_deadline("срочно", DAY) == "срочно"
    assert T._fix_lead_deadline(None, DAY) is None


def test_message_day_is_tashkent():
    msg = SimpleNamespace(date=datetime(2026, 9, 30, 21, 30, tzinfo=timezone.utc))  # 02:30 1 окт в Ташкенте
    assert T._tashkent_day(msg) == date(2026, 10, 1)


def test_prompt_carries_the_date():
    sent = {}

    class _R(object):
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "intent: demand\ndeadline: 01.10.2026"}}]}

    def _post(url, headers=None, json=None, timeout=None):
        sent["prompt"] = json["messages"][0]["content"]
        return _R()

    with patch.object(T._httpx, "post", _post):
        T._ai_extract_fields("Футболки 200 шт к 1 октября", "key", "m", today=DAY)
    assert sent["prompt"].startswith("Сегодня 28.09.2026 (Ташкент)."), sent["prompt"][:80]
