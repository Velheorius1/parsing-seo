"""Пины разведения чатовых лидов и площадочных встречных аукционов (29.07).

Оба несут `message_type="customer_request"`, но приходят из разных миров:
человек в Telegram пишет «нужны коробки» — и площадка публикует встречный
аукцион банка с ценой, заказчиком и дедлайном. До 29.07 второе судил промпт,
начинающийся словами «Это сообщение из Telegram-чата», а `_route_to_push`
пушил его в обход мьютов как «живого клиента».

Тесты держат ровно эту границу — и с обеих сторон: чатовый лид не должен
потерять свои привилегии (обход мьюта, порядок, кнопки), а площадочный лот не
должен их получить.

Run: python3 -m crawler.tests.test_lead_gate_split   (exit 1 on any failure)
"""
from datetime import datetime, timezone

from crawler.core.models import RawTender
from crawler.core.notifier import (
    _is_high_signal, _is_tg_lead, _route_to_push, prefilter,
)

TG = "TG: PR Media Group (запросы клиентов)"
XTX = "XT-Xarid встречные аукционы"
HAYOT = "Hayotbirja встречные аукционы"


def _mk(**k):
    k.setdefault("id", "t")
    k.setdefault("external_id", k.get("id", "t"))
    k.setdefault("title", "Коробка картонная")
    k.setdefault("organization", "")
    k.setdefault("source", TG)
    k.setdefault("message_type", "customer_request")
    k.setdefault("collected_at", datetime.now(timezone.utc))
    return RawTender(**k)


# ── сам предикат ──────────────────────────────────────────────────────────────

def test_tg_customer_request_is_lead():
    assert _is_tg_lead(_mk(source=TG)) is True


def test_reverse_auction_is_not_lead():
    # Главное, ради чего всё затевалось: закупка банка — не чатовый лид.
    assert _is_tg_lead(_mk(source=XTX)) is False
    assert _is_tg_lead(_mk(source=HAYOT)) is False


def test_tg_tender_channel_is_not_lead():
    # TG-канал тендеров шлёт message_type="tender" — это не лид.
    assert _is_tg_lead(_mk(source="TG: Beeline Tenders", message_type="tender")) is False


def test_new_tg_channel_counts_as_lead_by_prefix():
    # Предикат по префиксу, а не по списку: новый чат-канал попадёт в чатовый
    # гейт сам. Промах в эту сторону безопаснее — тендерный промпт режет
    # разговорные заказы, ради чего лид-гейт и вводился.
    assert _is_tg_lead(_mk(source="TG: Новый чат заказов")) is True


def test_missing_source_does_not_crash():
    assert _is_tg_lead(_mk(source="")) is False


# ── префильтр отдаёт ту же метку ──────────────────────────────────────────────

def test_prefilter_marks_only_tg_as_lead():
    tg = _mk(id="1", external_id="1", source=TG, title="Нужны коробки")
    lot = _mk(id="2", external_id="2", source=XTX, title="Картон конверт",
              price=234_850_000)
    pf = prefilter([tg, lot], ["коробк", "конверт"], tnved_scope=[])
    by_id = dict((v.tender.external_id, v) for v in pf.verdicts)
    assert by_id["1"].is_lead is True
    assert by_id["2"].is_lead is False, "встречный аукцион больше не лид"


# ── роутинг: привилегии остались у чатового, ушли у площадочного ──────────────

def test_tg_lead_still_overrides_mute():
    t = _mk(source=TG)
    assert _route_to_push(t, {TG}) is True, "живой клиент важнее мьюта источника"


def test_reverse_auction_no_longer_bypasses_mute():
    t = _mk(source=XTX, price=1_000_000)
    assert _route_to_push(t, {XTX}) is False


def test_reverse_auction_with_live_bids_still_pushes():
    # Замер 29.07: все 12 алерченных за неделю проходят общий роутинг.
    t = _mk(source=XTX, bid_count=3)
    assert _is_high_signal(t) is True
    assert _route_to_push(t, set()) is True


def test_big_ticket_reverse_auction_still_pushes():
    t = _mk(source=XTX, price=234_850_000)
    assert _route_to_push(t, set()) is True


def test_cheap_quiet_reverse_auction_goes_to_digest():
    t = _mk(source=XTX, price=6_000_000, bid_count=0, deadline=None)
    assert _route_to_push(t, set()) is False


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
