"""Regression guards for structured detail on the live alert path."""
import os
import sys
import types
from datetime import datetime, timezone

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70, ai_relevance_model="x",
        ai_relevance_model_fast="x", supabase_url="", supabase_service_role_key="",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core.models import RawTender
from crawler.core.notifier import _format_alert


def _tender(**changes):
    data = {
        "id": "uzex-prq-1", "external_id": "1", "title": "Услуги печатные",
        "organization": "Заказчик", "source": "UZEX Предквалификации",
        "collected_at": datetime.now(timezone.utc),
    }
    data.update(changes)
    return RawTender(**data)


def test_structured_lots_are_not_rendered_and_do_not_crash_formatter():
    tender = _tender(extra_info={
        "Регион": "Ташкент",
        "lots": [{"productName": "Подарочная корзина", "description": "Фрукты"}],
    })
    text = _format_alert(tender, "печать")
    assert "Регион: Ташкент" in text
    assert "productName" not in text and "Фрукты" not in text, "структура попала в текст"


def test_scalar_extra_info_values_are_rendered_without_crashing():
    tender = _tender(extra_info={
        "Количество": 100,
        "Срочный": True,
        "Примечание": None,
        "lots": [{"productName": "Служебная позиция"}],
    })

    text = _format_alert(tender, "печать")

    assert "Количество: 100" in text
    assert "Срочный: True" in text
    assert "Примечание: None" in text
    assert "productName" not in text and "lots:" not in text


def test_headline_shows_subject_in_alert_and_digest():
    """#9638 (09.10): заголовок «Одежда», а покупали сувениры с логотипом."""
    from crawler.core.notifier import _build_digest_text
    tender = _tender(title="Одежда", extra_info={
        "lots": [{"productName": "Сувениры с логотипом", "description": "Сервиз"}]})
    text = _format_alert(tender, "сувенир")
    assert "*Сувениры с логотипом*" in text, text
    assert "Одежда" not in text
    assert "*Сувениры с логотипом*" in _build_digest_text([tender])


def test_headline_without_lots_keeps_category():
    text = _format_alert(_tender(title="Одежда"), "сувенир")
    assert "*Одежда*" in text


def test_service_keys_get_russian_labels():
    """09.10: в алерте «displayid: …», «customerinn: …», «maxpart: …» —
    латинские ключи, у которых Markdown-экранирование съело подчёркивание."""
    text = _format_alert(_tender(extra_info={
        "address": "Самарканд шахар", "display_id": "261191370115164",
        "customer_inn": "201007782"}), "печать")
    assert "Адрес: Самарканд шахар" in text
    assert "Номер лота: 261191370115164" in text
    assert "ИНН заказчика: 201007782" in text
    for raw in ("displayid", "customerinn", "address:"):
        assert raw not in text, raw


def test_coop_keys_labeled_and_technical_hidden():
    text = _format_alert(_tender(source="Cooperation.uz Лоты", extra_info={
        "offer": "O1756899", "tnved": "4820900000", "measure": "штук",
        "min_part": 1, "max_part": 10000, "quantity": 5000, "unit_price": 2500,
        "certificate": False, "ref_supplier": "IMKONIYAT", "ref_supplier_tin": "310277694",
        "photo": "https://new.cooperation.uz/ocelot/contractfiles/a b.jpg",
        "screenshot_url": "https://x/s.jpg", "screenshot_at": "2026-09-22T09:41:53"}), "печать")
    for line in ("Оферта: O1756899", "ТН ВЭД: 4820900000", "Количество: 5000 штук",
                 "Цена за ед.: 2,500", "Сертификат: нет",
                 "Поставщик-ориентир: IMKONIYAT (ИНН 310277694)",
                 "Фото оферты: https://new.cooperation.uz/ocelot/contractfiles/a%20b.jpg"):
        assert line in text, (line, text)
    for raw in ("screenshot", "maxpart", "minpart", "refsupplier", "unitprice", "measure:"):
        assert raw not in text, raw


def test_coop_values_from_db_strings_are_formatted_too():
    """recheck берёт строку из базы — числа и булевы там уже строкой."""
    text = _format_alert(_tender(source="Cooperation.uz Лоты", extra_info={
        "unit_price": "2500", "certificate": "False"}), "печать")
    assert "Цена за ед.: 2,500" in text and "Сертификат: нет" in text, text


def test_photo_url_cannot_break_markdown():
    text = _format_alert(_tender(source="Cooperation.uz Лоты", extra_info={
        "photo": "https://new.cooperation.uz/ocelot/contractfiles/a_b [1].jpg"}), "печать")
    url = [l for l in text.split("\n") if l.startswith("Фото оферты: ")][0][len("Фото оферты: "):]
    assert not any(ch in url for ch in ("_", "*", "`", "[", " ")), url
    assert url.endswith("a%5Fb%20%5B1%5D.jpg"), url


def test_unknown_latin_key_keeps_word_break():
    text = _format_alert(_tender(extra_info={"new_field": "x"}), "печать")
    assert "new field: x" in text


def test_raw_tender_accepts_structured_detail_for_replay_contract():
    tender = _tender(extra_info={"lots": [{"productName": "Буклет"}]})
    assert tender.extra_info["lots"][0]["productName"] == "Буклет"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    raise SystemExit(1 if failures else 0)
