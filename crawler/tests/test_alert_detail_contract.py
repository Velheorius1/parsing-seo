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
    assert "Подарочная корзина" not in text


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
