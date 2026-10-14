"""Перевыставленная закупка досылается с пометкой «🔁», а не глушится (14.10.2026).

ЧТО БЫЛО. Дедуп против отправленного (основной прогон — 14 дней, recheck —
близнецы ±14 дней, #101) сравнивает ключ лота: источник, заказчик, слова
названия (у предквалификаций ещё сумма). Срок в ключ не входит. Поэтому
заказчик, выставивший ту же закупку заново с новым сроком, молчал: 14.10 из
166 близнецов 9 были именно такими (Булунгур хокимлиги 75 млн: срок 07.10 →
14.10, Xalq Bank 168 млн, Турткуль 17 млн…). Решение Данияра 14.10: досылать
с пометкой.

ЧТО ДЕРЖИМ. Ключ совпал и срок НОВЕЕ отправленного — лот проходит с
`repost_prev_deadline`; тот же срок, более ранний или неизвестный — дубль, как
раньше (SQB «Сувенир макети» без срока ушёл 6 раз — это не должно вернуться).
Перевыставление одно на ключ. Пометка видна в алерте и в строке дайджеста и не
пишется в базу.

Run: .venv/bin/python3 -m pytest crawler/tests/test_repost.py -q
"""
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import dedup as D  # noqa: E402
from crawler.core import notifier as N  # noqa: E402
from crawler.core.db import _tender_to_row  # noqa: E402
from crawler.core.models import RawTender  # noqa: E402
import crawler.scripts.recheck as R  # noqa: E402

SRC = "UZEX Предквалификации"
ORG = "Булунгур туман хокимлиги"
TITLE = "Услуги печатные и услуги по копированию звуко- и видеозаписей"


def _t(ext, deadline, **k):
    k.setdefault("title", TITLE)
    k.setdefault("organization", ORG)
    k.setdefault("source", SRC)
    k.setdefault("price", 75000000.0)
    return RawTender(id="uzex-prq-%s" % ext, external_id=ext, deadline=deadline, **k)


def _key(t):
    return D._within_source_key(t)


def _sent(deadline_day):
    k = _key(_t("sent", None))
    return {k}, {k: deadline_day}


# ── дедуп основного прогона и recheck ────────────────────────────────────────

def test_later_deadline_is_a_repost_and_passes_marked():
    keys, days = _sent("2026-10-07")
    out, dropped = D.dedup_within_source([_t("116500", "2026-10-14T18:00:00")], keys, days)
    assert dropped == 0 and len(out) == 1
    assert out[0].repost_prev_deadline == "2026-10-07"


def test_same_earlier_or_unknown_deadline_is_still_a_duplicate():
    keys, days = _sent("2026-10-14")
    for dl in ("2026-10-14T18:00:00", "2026-10-07", None, "срок уточняется"):
        out, dropped = D.dedup_within_source([_t("x", dl)], keys, days)
        assert out == [] and dropped == 1, dl


def test_unknown_sent_deadline_keeps_old_behaviour():
    keys, days = _sent(None)
    out, dropped = D.dedup_within_source([_t("x", "2026-10-30")], keys, days)
    assert out == [] and dropped == 1


def test_only_one_repost_per_key_in_a_batch():
    keys, days = _sent("2026-10-07")
    batch = [_t("a", "14.10.2026"), _t("b", "14.10.2026")]
    out, dropped = D.dedup_within_source(batch, keys, days)
    assert [t.external_id for t in out] == ["a"] and dropped == 1


def test_without_deadlines_dedup_is_unchanged():
    """recall_audit и прочие вызовы без сроков — прежнее поведение."""
    keys, _ = _sent("2026-10-07")
    out, dropped = D.dedup_within_source([_t("x", "2026-10-30")], keys)
    assert out == [] and dropped == 1
    out, _groups = D.group_for_alerts([_t("y", "2026-10-30")], [], recent_alerted_keys=keys)
    assert out == []


def test_group_for_alerts_passes_deadlines_through():
    keys, days = _sent("2026-10-07")
    out, _groups = D.group_for_alerts([_t("y", "2026-10-30")], [],
                                      recent_alerted_keys=keys, recent_alerted_deadlines=days)
    assert [t.repost_prev_deadline for t in out] == ["2026-10-07"]


def test_index_keeps_the_latest_sent_deadline(monkeypatch):
    rows = [dict(source=SRC, title=TITLE, organization=ORG, price=75000000.0, deadline=d)
            for d in ("2026-10-07", None, "2026-10-09T10:00:00")]

    class _Q(object):
        def __getattr__(self, name):
            return self

        def __call__(self, *a, **k):
            return self

        def execute(self):
            return types.SimpleNamespace(data=rows)

    fake = types.ModuleType("supabase")
    fake.create_client = lambda url, key: _Q()
    monkeypatch.setitem(__import__("sys").modules, "supabase", fake)
    from crawler.config.settings import settings
    monkeypatch.setattr(settings, "supabase_url", "http://x", raising=False)
    monkeypatch.setattr(settings, "supabase_service_role_key", "k", raising=False)
    idx = D.load_recent_alerted_index(days=14)
    assert list(idx.values()) == ["2026-10-09"], idx
    assert D.load_recent_alerted_fingerprints(days=14) == set(idx)


# ── близнецы второго шанса ───────────────────────────────────────────────────

def _row(ext, created, deadline):
    return dict(external_id=ext, source=SRC, organization=ORG, title=TITLE,
                price=75000000.0, deadline=deadline, created_at=created)


def test_recheck_twin_with_newer_deadline_is_kept_marked():
    alerted = [_row("sent", "2026-10-01T08:00:00+00:00", "2026-10-07T18:00:00")]
    rows = [_row("new", "2026-10-09T08:00:00+00:00", "2026-10-14T18:00:00"),
            _row("dup", "2026-10-02T08:00:00+00:00", "2026-10-07T18:00:00")]
    keep, twins = R.drop_alerted_twins(rows, alerted)
    assert [r["external_id"] for r in keep] == ["new"]
    assert keep[0]["_repost_prev_deadline"] == "2026-10-07"
    assert [r["external_id"] for r in twins] == ["dup"]


def test_recheck_twin_against_sent_without_deadline_stays_a_twin():
    alerted = [_row("sent", "2026-10-01T08:00:00+00:00", None)]
    keep, twins = R.drop_alerted_twins(
        [_row("new", "2026-10-09T08:00:00+00:00", "2026-10-14")], alerted)
    assert keep == [] and len(twins) == 1


# ── сообщение и база ─────────────────────────────────────────────────────────

def test_alert_and_digest_show_the_repost():
    t = _t("116500", "2026-10-14T18:00:00")
    t.repost_prev_deadline = "2026-10-07"
    text = N._format_alert(t, "печат")
    assert "🔁 *Перевыставлен* — уже приходил со сроком до 07.10.2026" in text, text
    assert "Дедлайн: 2026-10-14T18:00:00" in text
    assert "🔁" in N._build_digest_text([t])
    plain = _t("116501", "2026-10-14T18:00:00")
    assert "🔁" not in N._format_alert(plain, "печат")
    assert "🔁" not in N._build_digest_text([plain])


def test_marker_is_not_written_to_the_database():
    t = _t("116500", "2026-10-14T18:00:00")
    t.repost_prev_deadline = "2026-10-07"
    row = _tender_to_row(t)
    assert not [k for k in row if "repost" in k], row
    assert "repost" not in str(row.get("extra_info") or "")
