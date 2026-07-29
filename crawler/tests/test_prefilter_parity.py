"""Parity pins for the prefilter() extraction (2026-07-27).

send_alerts stages 2-11 were inlined for 5 months; replay/benchmark tooling needs
them as a pure function, and the extraction is only safe if behavior is pinned.
Every case here encodes the PRE-refactor behavior — including the quirks:

- the "below price threshold" counter is cumulative from the ORIGINAL input;
- the stale stage (>365d) is UNREACHABLE: it parses the same `deadline` field the
  expired gate (1-day grace) already dropped. A 400-day-old deadline dies at
  DEADLINE_EXPIRED, never at STALE. Pinned, not fixed — prod-log diffing depends
  on stage order staying exactly as it was.

Run: python3 -m crawler.tests.test_prefilter_parity   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta

# pydantic_settings is a prod-only dep — stub the settings module before the
# notifier import pulls it (same pattern as test_scout_store_roundtrip).
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
    DropStage,
    MIN_PRICE,
    prefilter,
    _is_deadline_expired,
)

KW = ["печать", "упаковка", "коробка", "этикетка", "полиграфия", "лента"]
NOW = datetime(2026, 7, 27, 12, 0, 0)


def _mk(**kw):
    base = dict(
        id="t1", external_id="t1", title="Печать буклетов для банка",
        organization="АО Заказчик", source="Hayotbirja отбор",
        search_text="печать буклетов", price=10_000_000, message_type="tender",
    )
    base.update(kw)
    return RawTender(**base)


def _run(tenders, **kw):
    kw.setdefault("keywords", KW)
    kw.setdefault("now", NOW)
    return prefilter(tenders, **kw)


# ── happy path ────────────────────────────────────────────────────────────────

def test_clean_tender_passes():
    r = _run([_mk()])
    assert r.counters["passed"] == 1 and not r.uzex_bypass
    v = r.verdicts[0]
    assert v.passed and v.dropped_at is None and v.matched_kw and not v.is_lead


def test_verdicts_are_one_to_one_and_counters_add_up():
    tenders = [
        _mk(id="a", external_id="a"),
        _mk(id="b", external_id="b", message_type="info"),
        _mk(id="c", external_id="c", price=1_000_000),
        _mk(id="d", external_id="d", title="Ремонт кровли", search_text="ремонт кровли"),
    ]
    r = _run(tenders)
    assert len(r.verdicts) == len(tenders)
    assert [v.tender.external_id for v in r.verdicts] == ["a", "b", "c", "d"]
    total = r.counters["passed"] + r.counters["bypass"] + sum(
        r.counters[s] for s in DropStage.ORDER)
    assert total == len(tenders), r.counters


# ── stages, in prod order ────────────────────────────────────────────────────

def test_info_message_type_drops_first():
    # message_type is the FIRST stage: an info row that would also fail price
    # must report message_type, not min_price.
    r = _run([_mk(message_type="info", price=1)])
    assert r.verdicts[0].dropped_at == DropStage.MESSAGE_TYPE


def test_tg_customer_request_is_lead():
    # Чатовый лид судится спам-гейтом — ради этого метка и существует.
    r = _run([_mk(message_type="customer_request",
                  source="TG: PR Media Group (запросы клиентов)")])
    assert r.verdicts[0].passed and r.verdicts[0].is_lead


def test_reverse_auction_customer_request_is_not_lead():
    # 29.07: площадочный встречный аукцион тоже несёт customer_request, но это
    # закупка с ценой и заказчиком — её судит тендерный тракт, а не промпт,
    # начинающийся словами «Это сообщение из Telegram-чата».
    r = _run([_mk(message_type="customer_request",
                  source="XT-Xarid встречные аукционы")])
    assert r.verdicts[0].passed and not r.verdicts[0].is_lead


def test_no_push_source_drops():
    r = _run([_mk(source="XT-Xarid э-магазин")])
    assert r.verdicts[0].dropped_at == DropStage.NO_PUSH_SOURCE


def test_own_lot_drops():
    r = _run([_mk(organization="ЧП WINCH GROUP")])
    assert r.verdicts[0].dropped_at == DropStage.OWN_LOT


def test_min_price_boundary():
    r = _run([
        _mk(id="low", external_id="low", price=MIN_PRICE - 1),
        _mk(id="edge", external_id="edge", price=MIN_PRICE),
        _mk(id="nil", external_id="nil", price=None),
    ])
    by_id = dict((v.tender.external_id, v) for v in r.verdicts)
    assert by_id["low"].dropped_at == DropStage.MIN_PRICE
    assert by_id["edge"].passed and by_id["nil"].passed


def test_deadline_none_passes_and_today_survives_grace():
    # Date-only parse: "27.07.2026" is 00:00, i.e. 12h "past" at NOW noon — the
    # 1-day grace keeps it. (A yesterday-dated deadline is 36h past → expired.)
    r = _run([
        _mk(id="none", external_id="none", deadline=None),
        _mk(id="today", external_id="today", deadline=NOW.strftime("%d.%m.%Y")),
    ])
    assert all(v.passed for v in r.verdicts), [v.dropped_at for v in r.verdicts]


def test_deadline_two_days_ago_expires():
    r = _run([_mk(deadline=(NOW - timedelta(days=2)).strftime("%d.%m.%Y"))])
    assert r.verdicts[0].dropped_at == DropStage.DEADLINE_EXPIRED


def test_stale_stage_is_dead_400_days_reports_deadline_expired():
    # THE pin: 400 days past dies at the expired gate, STALE never fires.
    r = _run([_mk(deadline=(NOW - timedelta(days=400)).strftime("%d.%m.%Y"))])
    assert r.verdicts[0].dropped_at == DropStage.DEADLINE_EXPIRED
    assert r.counters[DropStage.STALE] == 0


def test_no_keyword_drops():
    r = _run([_mk(title="Ремонт отопления", search_text="ремонт отопления котельной")])
    assert r.verdicts[0].dropped_at == DropStage.NO_KEYWORD


def test_tnved_fallback_matches_without_keywords():
    t = _mk(title="Mahsulot yetkazib berish", search_text="mahsulot",
            extra_info={"tnved": "4821901000"})
    assert _run([t]).verdicts[0].dropped_at == DropStage.NO_KEYWORD  # scope off
    r = _run([t], tnved_scope=["4821"])
    v = r.verdicts[0]
    assert v.passed and v.matched_kw == "тнвэд:4821", v


def test_reject_title_drops():
    r = _run([_mk(title="Марля полиграфическая 100м", search_text="марля полиграфическая")])
    assert r.verdicts[0].dropped_at == DropStage.REJECT_TITLE


def test_uzex_bypass_by_source_and_hint():
    uzex = _mk(id="u", external_id="u", source="UZEX Предквалификации",
               title="Услуги печатные прочие")
    other = _mk(id="o", external_id="o", source="Hayotbirja отбор",
                title="Услуги печатные прочие")
    r = _run([uzex, other])
    by_id = dict((v.tender.external_id, v) for v in r.verdicts)
    assert by_id["u"].uzex_bypass and by_id["u"].passed
    assert not by_id["o"].uzex_bypass and by_id["o"].passed
    assert len(r.uzex_bypass) == 1 and len(r.matching) == 1


# ── now-injection (the one extension) ────────────────────────────────────────

def test_now_injection_makes_history_judgeable():
    """A June tender judged as of June passes; judged as of wall-clock-now expires."""
    june = datetime(2026, 6, 10, 12, 0, 0)
    t = _mk(deadline="15.06.2026")
    assert _run([t], now=june).verdicts[0].passed
    assert _run([t], now=NOW).verdicts[0].dropped_at == DropStage.DEADLINE_EXPIRED


def test_deadline_expired_default_now_unchanged():
    """No-now call keeps wall-clock semantics (prod call sites pass nothing)."""
    t = _mk(deadline="01.01.2020")
    assert _is_deadline_expired(t) is True
    t2 = _mk(deadline=None)
    assert _is_deadline_expired(t2) is False


# ── batch-level parity ───────────────────────────────────────────────────────

def test_matching_preserves_input_order():
    tenders = [_mk(id=str(i), external_id=str(i)) for i in range(5)]
    r = _run(tenders)
    assert [t.external_id for t, _ in r.matching] == ["0", "1", "2", "3", "4"]


def test_empty_keyword_batch_returns_empty_not_crash():
    r = _run([_mk(title="Ремонт", search_text="ремонт")])
    assert r.matching == [] and r.uzex_bypass == []
    assert r.counters["passed"] == 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, "-", str(e)[:140])
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
