"""Regression guards for alert signal-vs-noise (deep-think 2026-07-01).

Pure-logic tests (no DB/network) protecting the recall-critical boundaries:
dedup collapse WITHOUT false-merge, own-lot suppression, push-vs-digest routing.
The feedback→auto-mute ✅-veto is an integration test (needs session_store); run it
live per docs/weekly/ROUTINE.md ШАГ 4.5.

Run: python3 -m crawler.tests.test_alert_routing   (exit 1 on any failure)
"""
from datetime import datetime, timezone, timedelta

from crawler.core.models import RawTender
from crawler.core.dedup import _logical_key, dedup_within_source
from crawler.core.notifier import _is_high_signal, _is_own_lot, _route_to_push


def _mk(**k):
    k.setdefault("id", "t")
    k.setdefault("external_id", k.get("id", "t"))
    k.setdefault("title", "Лот")
    k.setdefault("organization", "")
    k.setdefault("source", "TG: PR Media Group (запросы клиентов)")
    k.setdefault("collected_at", datetime.now(timezone.utc))
    return RawTender(**k)


def test_empty_org_reposts_collapse():
    # The 40%-duplicate root cause: TG reposts with no org must NOT re-alert.
    a = _mk(id="1", external_id="1", title="KOMRON PRESS реклама")
    b = _mk(id="2", external_id="2", title="KOMRON PRESS реклама")
    out, dropped = dedup_within_source([a, b])
    assert len(out) == 1 and dropped == 1


def test_spec_token_same_key():
    assert _logical_key("X", "", "чек лента", None, None) == \
           _logical_key("X", "", "чек лента 80мм", None, None)


def test_distinct_short_leads_not_merged():
    # Recall: two genuinely different short leads must both survive.
    out, _ = dedup_within_source([_mk(id="1", title="бланк"), _mk(id="2", title="бейдж")])
    assert len(out) == 2


def test_org_dup_still_collapses():
    out, _ = dedup_within_source([
        _mk(id="a", title="Учебники", organization="Школа 5"),
        _mk(id="b", title="Учебники", organization="Школа 5"),
    ])
    assert len(out) == 1


def test_never_alerted_survives():
    # Recall: a lot not in the recent-alerted set gets its first alert.
    out, _ = dedup_within_source([_mk(id="new", title="Газета", organization="Янги Узбекистон")],
                                 keep_existing_keys=set())
    assert len(out) == 1


def test_own_lot_suppressed():
    assert _is_own_lot("WINCH GROUP XK")
    assert _is_own_lot("ЧП Винч")
    assert not _is_own_lot("KARTON PAPER BUSINESS")
    assert not _is_own_lot("")


def test_high_signal_routing():
    far = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
    soon = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")  # closes tomorrow (deterministic)
    assert _is_high_signal(_mk(message_type="customer_request"))                 # hot lead → push
    assert _is_high_signal(_mk(price=200_000_000, deadline=far, source="X"))     # big-ticket → push
    assert _is_high_signal(_mk(deadline=soon, source="X"))                       # closes tomorrow → push
    # relevant but not urgent/huge → digest (the over-push we tightened away)
    assert not _is_high_signal(_mk(relevance_score=88, price=20_000_000, deadline=far, source="X"))


def test_muted_coop_source_routes_to_digest():
    # Coop unification 2026-07-22: the exact bug class this project spent a week
    # killing — a muted source that still pushes. Once coop flows through the shared
    # pipeline, a mute on 'Cooperation.uz Лоты' must actually route it to digest.
    soon = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
    t = _mk(source="Cooperation.uz Лоты", deadline=soon)  # closes tomorrow = high signal
    assert _is_high_signal(t)                                       # would push unmuted
    assert not _route_to_push(t, {"Cooperation.uz Лоты"})           # mute wins → digest
    assert _route_to_push(t, set())                                 # no mute → push


def test_tg_lead_overrides_mute():
    # A real client asking to buy NOW is recall we never trade away.
    t = _mk(message_type="customer_request", source="TG: PR Media Group (запросы клиентов)")
    assert _route_to_push(t, {"TG: PR Media Group (запросы клиентов)"})


def test_platform_customer_request_does_not_override_mute():
    # 29.07: привилегия «пушить в обход мьюта» принадлежит ЧАТОВОМУ лиду.
    # Площадочный встречный аукцион несёт ту же метку, но это закупка —
    # замер показал, что все алерченные за неделю проходят и общий роутинг.
    t = _mk(message_type="customer_request", source="Cooperation.uz Лоты", price=6_000_000)
    assert not _route_to_push(t, {"Cooperation.uz Лоты"})


def test_supplier_catalog_never_pushes():
    # Sell-side listings (asking price ≠ demand): the big-ticket override must NOT
    # leak them back (e-shop leak 2026-07-03; Оферты joined the pipeline 2026-07-22).
    far = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
    assert not _is_high_signal(_mk(source="Cooperation.uz Оферты", price=298_000_000, deadline=far))
    assert not _is_high_signal(_mk(source="Cooperation.uz Э-магазин лоты", price=200_000_000, deadline=far))



def test_strong_lot_without_price_and_deadline_pushes():
    """Решение Данияра 04.08. До этого КАЖДОЕ условие пуша требовало цену либо
    дедлайн, поэтому банки и корпоративные сайты не могли получить пуш в
    принципе — они не публикуют ни того, ни другого. Конверты для банковских
    карт от Anor Bank со score 90 уходили строкой в дайджест «не требуют
    мгновенной реакции». Замер: 12 таких лотов за 14 дней = 0,9 в сутки."""
    t = _mk(source="Anor Bank", title="Изготовление конвертов для банковских карт",
            relevance_score=90, price=None, deadline=None)
    assert _is_high_signal(t)


def test_weak_lot_without_price_stays_in_digest():
    """Порог 90 обязан отсекать: иначе в пуш уедет весь бесценовой поток."""
    t = _mk(source="Anor Bank", relevance_score=85, price=None, deadline=None)
    assert not _is_high_signal(t)
    assert not _is_high_signal(_mk(source="Anor Bank", relevance_score=None,
                                   price=None, deadline=None))


def test_supplier_catalog_without_price_still_never_pushes():
    """Новое правило стоит ПОСЛЕ отсечки каталогов — она сильнее (locked-решение
    Данияра: e-shop только по старту аукциона)."""
    assert not _is_high_signal(_mk(source="Cooperation.uz Оферты",
                                   relevance_score=95, price=None, deadline=None))
    assert not _is_high_signal(_mk(source="Cooperation.uz Э-магазин лоты",
                                   relevance_score=95, price=None, deadline=None))


def test_strong_lot_with_price_is_unaffected_by_the_new_rule():
    """Лот С ценой ниже порогов остаётся в дайджесте, как и раньше."""
    far = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    assert not _is_high_signal(_mk(source="X", relevance_score=90,
                                   price=20_000_000, deadline=far))

if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError:
            print("FAIL", fn.__name__)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
