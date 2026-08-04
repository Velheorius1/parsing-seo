"""Пины еженедельной банковской сводки (04.08).

Три свойства, каждое из которых уже стоило чего-то в этом проекте:

1. Актуальность считается по ДВУМ полям. Банки хранят разное: у одних настоящая
   «Дата истечения», у других срока нет в природе и мы кладём дату публикации в
   date_start. Проверка по одному полю вернула бы либо архив, либо пустоту —
   замер 04.08: 90 архивных лотов против 7 актуальных.
2. Лот без обеих дат в сводку НЕ попадает. Иначе туда затечёт весь архив
   (Туронбанк 2019, Микрокредитбанк 2022, Пойтахт 2024).
3. Сводка формируется ДАЖЕ когда нового нет. Урок дня: отсутствие сигнала
   неотличимо от отсутствия события — молчащая рассылка выглядит ровно как
   сломанный крон.

Run: python3 -m crawler.tests.test_bank_digest   (exit 1 on any failure)
"""
import sys
import types
from datetime import date


def _load():
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(
            alert_keywords=["картон"], telegram_bot_token="",
            telegram_alert_chat_id="", supabase_url="", openrouter_api_key="",
            supabase_service_role_key="")
        sys.modules[cfg] = m
    from crawler.scripts import bank_digest
    return bank_digest


BD = _load()
TODAY = date(2026, 8, 4)


def test_open_deadline_is_fresh():
    ok, why = BD.freshness({"deadline": "19.08.2026"}, TODAY)
    assert ok and "до 19.08.2026" in why, why


def test_expired_deadline_is_not_fresh():
    """Asia Alliance Bank: 19.06.2026 выглядит «2026-м», но истёк полтора месяца назад."""
    ok, _ = BD.freshness({"deadline": "19.06.2026"}, TODAY)
    assert not ok


def test_recent_publication_is_fresh_without_any_deadline():
    """Anor Bank: срока подачи на странице нет вообще, есть только публикация."""
    ok, why = BD.freshness({"deadline": None, "date_start": "30.07.2026"}, TODAY)
    assert ok and "опубликован 30.07.2026" in why, why


def test_old_publication_is_not_fresh():
    ok, _ = BD.freshness({"deadline": None, "date_start": "19.07.2026"}, TODAY)
    assert not ok, "19.07 — это 16 дней назад, за окном в 7 дней"


def test_lot_without_any_date_never_enters_the_digest():
    assert not BD.freshness({"deadline": None, "date_start": None}, TODAY)[0]
    assert not BD.freshness({}, TODAY)[0]


def test_deadline_wins_over_stale_publication():
    """Открытый срок делает лот актуальным, даже если опубликован давно."""
    ok, why = BD.freshness({"deadline": "01.09.2026", "date_start": "01.01.2026"}, TODAY)
    assert ok and why.startswith("до "), why


def test_window_is_configurable():
    row = {"deadline": None, "date_start": "19.07.2026"}
    assert not BD.freshness(row, TODAY, fresh_days=7)[0]
    assert BD.freshness(row, TODAY, fresh_days=30)[0]


# ── сообщение ────────────────────────────────────────────────────────────────

def test_empty_week_still_produces_a_message():
    msgs = BD.build_message([], 0, 0, ["Хамкорбанк"])
    assert len(msgs) == 1
    assert "новых лотов: 0" in msgs[0]
    assert "Хамкорбанк" in msgs[0]


def test_message_marks_already_sent_and_profile_hits():
    groups = [("Anor Bank", [
        ({"title": "Конверты для банковских карт", "source_url": "https://x.uz/1",
          "alert_seq": 6909}, "опубликован 30.07.2026"),
        ({"title": "Наклейки A5", "source_url": "https://x.uz/2",
          "relevance_score": 90}, "опубликован 30.07.2026"),
        ({"title": "Уборка офиса", "source_url": "https://x.uz/3",
          "relevance_score": 10}, "до 19.08.2026"),
    ])]
    text = "\n".join(BD.build_message(groups, 3, 12, []))
    assert "✅уже присылал" in text
    assert "⭐наш профиль" in text
    assert text.count("⭐") == 1, "низкий score не должен получать звезду"
    assert "https://x.uz/3" in text


def test_long_digest_is_split_under_telegram_limit():
    big = [("Банк %d" % i, [({"title": "Лот " + "я" * 80,
                              "source_url": "https://x.uz/%d" % i}, "до 19.08.2026")
                            for _ in range(6)]) for i in range(12)]
    msgs = BD.build_message(big, 72, 0, [])
    assert len(msgs) > 1, "длинная сводка обязана разбиваться"
    assert all(len(m) <= BD.TG_LIMIT + 200 for m in msgs), [len(m) for m in msgs]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
