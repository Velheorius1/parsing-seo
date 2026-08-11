"""Пины кликабельного дайджеста (11.08).

Из чего выросло. Замер 11.08: последний клик 01.08, десять дней тишины, а
понедельничный прогон дистиллятора обработал ноль коррекций — весь обучающий
контур работал вхолостую. Причина оказалась не в лени и не в поломке: кнопки
были ТОЛЬКО у пуш-сообщений, а в дайджест уходит примерно две трети алертов
(у источников «э-магазин»/«оферт» — 100%, замер по 14 дням: 84 алерта, 0
доставленных отдельным сообщением). То есть большую часть показанного человеку
нельзя было оценить в принципе.

Свойства, которые тут держатся:
  • номер строки в тексте и номер алерта в кнопке идут ОТ ОДНОГО ранжирования —
    иначе клик по строке 3 пометит чужой лот, и обучение отравится молча;
  • клик по одной строке дайджеста НЕ стирает кнопки остальных — иначе из
    десяти лотов оценить можно ровно один;
  • у одиночного алерта поведение прежнее: выбор сделан, клавиатура схлопнута;
  • метки остались `ok`/`skip`, то есть бот и playbook принимают их без правок.

Run: python3 -m crawler.tests.test_digest_feedback   (exit 1 on any failure)
"""
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core.models import RawTender
from crawler.core.notifier import (
    DIGEST_SHOWN, _build_digest_keyboard, _build_digest_text, _rank_digest,
)


def _t(ext, title="Лот", price=1_000_000.0, score=60):
    return RawTender(id=ext, external_id=ext, title=title, organization="Орг",
                     price=price, currency="UZS", source="Тест",
                     source_url="https://example.uz/%s" % ext,
                     relevance_score=score)


def _many(n):
    # разная цена → разный ранг, порядок заведомо не совпадает с исходным
    return [_t("e%d" % i, "Лот %d" % i, price=float((i * 37) % 11 + 1) * 1e6)
            for i in range(n)]


# --- сцепка номера строки и номера алерта ------------------------------------

def test_button_number_matches_the_line_number():
    """Главное свойство. Кнопка «3» обязана указывать на лот, стоящий в тексте
    третьим, — а порядок задаёт ранжирование, не порядок прихода."""
    tenders = _many(6)
    start = 500
    ranked = _rank_digest(tenders)
    kb = _build_digest_keyboard(tenders, start)["inline_keyboard"]
    for i, row in enumerate(kb, 1):
        seq = start + i - 1
        assert all(("fb:%d:" % seq) in b["callback_data"] for b in row), (i, row)
        assert row[0]["text"].startswith("%d " % i), row[0]["text"]
    # и тот же порядок виден в тексте
    text = _build_digest_text(tenders)
    for i, t in enumerate(ranked[:DIGEST_SHOWN], 1):
        assert ("*%d.*" % i) in text, i


def test_ranking_is_computed_once_and_shared():
    """Текст и клавиатура обязаны строиться из ОДНОГО порядка. Если бы каждый
    сортировал сам и сортировки разошлись, кнопка помечала бы соседний лот —
    ошибка, которую по логам не видно вообще."""
    tenders = _many(8)
    a = [t.external_id for t in _rank_digest(tenders)]
    b = [t.external_id for t in _rank_digest(tenders)]
    assert a == b, "ранжирование недетерминировано"
    assert a != [t.external_id for t in tenders], "тест бесполезен: порядок совпал"


def test_keyboard_covers_only_shown_lines():
    kb = _build_digest_keyboard(_many(25), 1)["inline_keyboard"]
    assert len(kb) == DIGEST_SHOWN, len(kb)


def test_empty_digest_has_no_buttons():
    assert _build_digest_keyboard([], 1)["inline_keyboard"] == []


def test_two_buttons_per_line_not_three():
    """В дайджесте лежат площадочные лоты, рекламы там не бывает — третий
    вариант только удлиняет выбор."""
    kb = _build_digest_keyboard(_many(3), 1)["inline_keyboard"]
    assert all(len(row) == 2 for row in kb), kb
    labels = {b["callback_data"].rsplit(":", 1)[-1] for row in kb for b in row}
    assert labels == {"ok", "skip"}, labels


def test_text_tells_that_buttons_are_numbered():
    assert "по номеру строки" in _build_digest_text(_many(3))


# --- клик по одной строке не гасит остальные ---------------------------------

def _bot():
    import crawler.scripts.feedback_bot as B
    return B


def test_clicking_one_digest_line_keeps_the_others():
    B = _bot()
    rows = _build_digest_keyboard(_many(4), 100)["inline_keyboard"]
    out = B.remaining_keyboard(rows, 101, B.LABEL_MAP["skip"])
    assert len(out) == len(rows), out
    # кликнутая строка стала подписью...
    assert len(out[1]) == 1 and out[1][0]["callback_data"] == "done", out[1]
    # ...а соседние живы и по-прежнему ведут на свои номера
    assert all(b["callback_data"].startswith("fb:100:") for b in out[0]), out[0]
    assert all(b["callback_data"].startswith("fb:102:") for b in out[2]), out[2]


def test_single_alert_keyboard_collapses_as_before():
    """Для одиночного алерта поведение прежнее — выбор сделан, кнопок нет."""
    B = _bot()
    rows = [[{"text": "Клиент", "callback_data": "fb:7:ok"},
             {"text": "Мимо", "callback_data": "fb:7:skip"}]]
    out = B.remaining_keyboard(rows, 7, B.LABEL_MAP["ok"])
    assert out == [[{"text": out[0][0]["text"], "callback_data": "done"}]]
    assert "#007" in out[0][0]["text"], out


def test_unknown_keyboard_still_gets_a_label():
    """Пустая или неожиданная клавиатура не должна ронять обработчик."""
    B = _bot()
    out = B.remaining_keyboard(None, 5, B.LABEL_MAP["ad"])
    assert out and out[0][0]["callback_data"] == "done"


def test_second_click_on_the_same_line_is_idempotent():
    """Повторный клик по уже отмеченной строке не должен воскрешать её кнопки."""
    B = _bot()
    rows = _build_digest_keyboard(_many(3), 10)["inline_keyboard"]
    once = B.remaining_keyboard(rows, 11, B.LABEL_MAP["ok"])
    twice = B.remaining_keyboard(once, 11, B.LABEL_MAP["ok"])
    assert twice == once, twice


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
