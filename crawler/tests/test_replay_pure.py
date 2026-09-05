"""Guards for the replay engine: it must NEVER touch the write path (2026-07-27).

The whole value of replay is that it can be pointed at anything — historical
rows, the frozen benchmark corpus — without burning alert_seq numbers, writing
relevance fields, sending Telegram or reading/writing the mute cache. These
tests pin that: the seq/persist functions raise if touched, mutes default to an
injected empty set, and the bypass branch delivers without an AI client.

Run: python3 -m crawler.tests.test_replay_pure   (exit 1 on any failure)
"""
import asyncio
import sys
import types
from datetime import datetime

# Stubs BEFORE any crawler import (prod-only deps + poisoned side effects).
if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="печать,упаковка", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = _m


def _poison(*_a, **_k):
    raise AssertionError("side-effect path touched from replay")


_fb = types.ModuleType("crawler.core.feedback")
_fb.get_next_seq = _poison
_fb.save_alert_seq = _poison
_fb.get_active_mutes = _poison
_fb.get_relevance_playbook = lambda limit=20: ""
# Заглушка остаётся в sys.modules до конца ВСЕГО прогона и достаётся каждому,
# кто импортирует этот модуль позже. Значит она обязана нести все имена, что
# есть у настоящего модуля, — иначе ломается не replay, а чужой тест, и
# виноватым выглядит он. Так 11.08 упали четыре теста дайджеста: feedback_bot
# импортирует record_feedback, которого здесь не было. Смысл заглушки при этом
# цел: вызов по-прежнему взрывается, просто на вызове, а не на импорте.
_fb.record_feedback = _poison
sys.modules["crawler.core.feedback"] = _fb

_db = types.ModuleType("crawler.core.db")
_db._get_client = lambda: None
_db.query_with_retry = _poison
_db.update_relevance_fields = _poison
_db.iter_rows = _poison
sys.modules["crawler.core.db"] = _db

from crawler.scripts.replay import ReplayVerdict, replay_tenders, row_to_raw_tender  # noqa: E402
from crawler.core.models import RawTender  # noqa: E402

KW = ["печать", "упаковка"]
NOW = datetime(2026, 7, 27, 12, 0)


def _mk(**kw):
    base = dict(
        id="t1", external_id="t1", title="Печать буклетов",
        organization="АО Заказчик", source="Hayotbirja отбор",
        search_text="печать буклетов", price=10_000_000, message_type="tender",
    )
    base.update(kw)
    return RawTender(**base)


def _run(tenders, **kw):
    kw.setdefault("keywords", KW)
    kw.setdefault("tnved_scope", [])
    kw.setdefault("as_of", "now")
    return asyncio.run(replay_tenders(tenders, **kw))


# ── purity ────────────────────────────────────────────────────────────────────

def test_prefilter_only_run_touches_nothing():
    """use_ai=False: verdicts come back and every poisoned function stays cold."""
    out = _run([_mk(), _mk(id="noise", external_id="noise", title="Ремонт кровли",
                           search_text="ремонт кровли")])
    assert len(out) == 2
    ok, bad = out[0], out[1]
    assert ok.passed_prefilter and ok.delivered is None and ok.ai_skipped
    assert not bad.passed_prefilter and bad.delivered is False
    assert bad.dropped_at_stage == "no_keyword"


def test_injected_keywords_and_scope_keep_replay_offline():
    """With keywords+tnved_scope given, replay must not consult settings/DB."""
    out = _run([_mk(extra_info={"tnved": "4821901000"}, title="Mahsulot",
                    search_text="mahsulot")], tnved_scope=["4821"])
    assert out[0].passed_prefilter and out[0].matched_kw == "тнвэд:4821"


def test_bypass_delivers_without_ai_client():
    """UZEX bypass = annotate-not-gate: delivered even in prefilter-only mode."""
    out = _run([_mk(source="UZEX Предквалификации", title="Услуги печатные прочие")])
    v = out[0]
    assert v.uzex_bypass and v.delivered is True and v.ai_skipped
    assert v.route in ("push", "digest")


def test_mutes_default_empty_and_injectable():
    t = _mk(source="UZEX Предквалификации", title="Услуги печатные прочие",
            price=200_000_000)
    no_mutes = _run([t])[0]
    muted = _run([_mk(source="UZEX Предквалификации", title="Услуги печатные прочие",
                      price=200_000_000)], mutes={"UZEX Предквалификации"})[0]
    assert no_mutes.delivered and muted.delivered            # mutes gate PUSH, not delivery
    assert muted.route == "digest"                           # ...but they demote the tier


def test_verdict_shape_is_stable():
    v = _run([_mk()])[0]
    for f in ("external_id", "source", "title", "passed_prefilter", "dropped_at_stage",
              "matched_kw", "uzex_bypass", "is_lead", "ai_score", "ai_category",
              "ai_error", "ai_skipped", "delivered", "route"):
        assert hasattr(v, f), f


# ── row conversion ────────────────────────────────────────────────────────────

def test_row_to_raw_tender_coerces_jsonb_types():
    """int/bool/None in extra_info must not crash RawTender (af1c155 trap)."""
    t = row_to_raw_tender({
        "external_id": "x1", "source": "S", "title": "T", "organization": None,
        "price": 7_000_000, "extra_info": {"Кол-во": 250, "Сертификат": True, "Пусто": None},
    })
    assert t.extra_info == {"Кол-во": "250", "Сертификат": "True"}
    assert t.organization == ""


def test_jsonb_list_does_not_explode_the_mapper():
    """Ночной аудит полноты падал на этом с 24.08 (замечено 05.09).

    `extra_info.lots` — список (обогащение предквалификаций), а RawTender ждёт
    Dict[str, str]. У recall_audit была своя копия маппинга без приведения
    типов, и `--execute` валился целиком: сторож полноты молча не работал две
    недели. Копия убрана, маппинг один на всех — в core/tender_rows.py.
    """
    t = row_to_raw_tender({
        "external_id": "77", "title": "Услуги издательские",
        "source": "UZEX Предквалификации",
        "extra_info": {"lots": [{"id": 338097, "productName": "Публикация статьи"}]},
    })
    assert isinstance(t.extra_info["lots"], str), "список снова уходит в pydantic как есть"
    assert "Публикация статьи" in t.search_text, "предмет лота потерян"


def test_hot_paths_do_not_import_replay_for_the_mapper():
    """Импорт replay выставляет PARSING_AI_LOG. Боевой путь recheck брал оттуда
    только маппинг и зависел от того, что notifier импортирован строкой выше и
    путь лога уже зафиксирован. Работало, но держалось на порядке строк."""
    import io, os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    for name in ("recheck.py", "customer_audit.py", "recall_audit.py"):
        body = io.open(os.path.join(root, name), encoding="utf-8").read()
        assert "from crawler.scripts.replay import row_to_raw_tender" not in body, name


def test_recall_audit_uses_the_shared_mapper():
    """Пин на источник: своя копия маппинга не должна вернуться."""
    import io, os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "recall_audit.py")
    body = io.open(path, encoding="utf-8").read()
    assert "from crawler.core.tender_rows import row_to_raw_tender" in body
    assert "return RawTender(" not in body, "recall_audit снова собирает RawTender сам"


def test_row_to_raw_tender_survives_minimal_row():
    t = row_to_raw_tender({"title": "Только заголовок"})
    assert t.external_id and t.source == "" and t.message_type == "tender"


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
