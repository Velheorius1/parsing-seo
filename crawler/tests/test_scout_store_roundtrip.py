"""Guard: what SCOUT stores, SCOUT can read back (2026-07-26).

THE HOLE: session_store.get_setting returns ONLY dicts — any other JSON shape is
parsed and then silently dropped. source_scout stored a bare LIST of candidates, so
from its first run (18.07) every Monday looked like this:

    06:00  scan   → ">>> stored 4 candidates ... to crawler_settings"
    06:10  report → "нет новых кандидатов (охват актуален)"   → sent to Telegram

Write said success, read said empty, and the weekly report announced an all-clear
while sitting on 4 unresolved proposals. The propose-only loop could never propose.

Pinned here: the dict envelope survives a round-trip, the drop-shape (None) degrades
to [] instead of exploding, and an investigated seed carrying a `verdict` is never
probed or re-proposed again.

Run: python3 -m crawler.tests.test_scout_store_roundtrip   (exit 1 on any failure)
"""
import sys
import types


def _load():
    """Import source_scout with the network-touching deps stubbed out."""
    name = "crawler.auth.session_store"
    if name not in sys.modules:
        m = types.ModuleType(name)
        m.session_store = types.SimpleNamespace(get_setting=lambda k: None,
                                                set_setting=lambda k, v: True)
        sys.modules[name] = m
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:      # pydantic_settings is a prod-only dep
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(telegram_bot_token="", telegram_alert_chat_id="",
                                           openrouter_api_key="test-key")
        sys.modules[cfg] = m
    import crawler.scripts.source_scout as S
    return S


S = _load()


def _stub_store(payloads):
    """Wire get/set_setting to a dict of key -> stored value."""
    S.session_store.get_setting = lambda k: payloads.get(k)

    def _set(k, v):
        payloads[k] = v
        return True

    S.session_store.set_setting = _set
    return payloads


# ── the round-trip contract ───────────────────────────────────────────────────

def test_dict_envelope_survives_the_round_trip():
    _stub_store({S.CAND_KEY: {"items": [{"name": "x"}, {"name": "y"}], "last_scan": "2026-07-26"}})
    assert len(S._load_candidates()) == 2, S._load_candidates()


def test_dropped_list_reads_as_empty_not_crash():
    # The exact production shape: a bare list was stored, get_setting returned None.
    _stub_store({S.CAND_KEY: None})
    assert S._load_candidates() == []


def test_legacy_bare_list_is_tolerated():
    _stub_store({S.CAND_KEY: [{"name": "hand-written"}]})
    assert len(S._load_candidates()) == 1


def test_envelope_without_items_is_empty():
    _stub_store({S.CAND_KEY: {"last_scan": "2026-07-26"}})
    assert S._load_candidates() == []


def test_scan_stores_what_the_report_will_read():
    """The bug in one assertion: scan() writes, _load_candidates() reads, counts match."""
    store = _stub_store({})
    S._known_hosts = lambda: set()
    S._probe = lambda url: {"alive": True, "status": 200, "relevant": True,
                            "has_print": False, "final_host": "etender.uzex.uz"}
    summary = S.scan(dry=False)
    assert S.CAND_KEY in store, "scan stored nothing"
    assert len(S._load_candidates()) == summary["candidates"], (
        "stored %d, read back %d" % (summary["candidates"], len(S._load_candidates())))


# ── the verdict lifecycle ─────────────────────────────────────────────────────

def test_investigated_seed_is_never_reproposed():
    store = _stub_store({})
    S._known_hosts = lambda: set()
    probed = []

    def _probe(url):
        probed.append(url)
        return {"alive": True, "status": 200, "relevant": True, "has_print": False,
                "final_host": "etender.uzex.uz"}

    S._probe = _probe
    S.scan(dry=False)
    closed_urls = [s["url"] for s in S.SEED if s.get("verdict")]
    assert closed_urls, "no seed carries a verdict — the fixture is stale"
    for u in closed_urls:
        assert u not in probed, "closed candidate re-probed: %s" % u
    names = [c["name"] for c in S._load_candidates()]
    for s in S.SEED:
        if s.get("verdict"):
            assert s["name"] not in names, "closed candidate re-proposed: %s" % s["name"]


def test_report_keeps_the_verdict_visible():
    _stub_store({S.CAND_KEY: {"items": []}})
    body = S._fmt_report()
    assert "Закрыто разведкой" in body, body
    for s in S.SEED:
        if s.get("verdict"):
            assert s["name"] in body, "verdict for %s not surfaced" % s["name"]


def test_verdict_note_is_not_cut_at_a_hostname_dot():
    """First cut used note.split('.')[0] — the NIM verdict rendered as the word 'nim',
    because its note opens with the hostname 'nim.uz'. A clipped verdict is useless."""
    note = "nim.uz — сайт Института метрологии, не портал закупок. Страница пуста."
    assert S._clip(note, 200).startswith("nim.uz — сайт Института"), S._clip(note, 200)
    long = S._clip("слово " * 60, 150)
    assert len(long) <= 151 and long.endswith("…"), (len(long), long[-20:])


def test_every_verdict_is_self_explaining():
    """A verdict without a reason is worse than none — it blocks a re-check silently."""
    for s in S.SEED:
        v = s.get("verdict")
        if not v:
            continue
        assert v.get("date") and v.get("outcome"), s["name"]
        assert len(v.get("note") or "") >= 40, "verdict note too thin: %s" % s["name"]


# ── open-web discovery (--discover) ───────────────────────────────────────────

def _stub_model(items, cost=0.005):
    """Make the OpenRouter call return `items` as a fenced JSON array."""
    body = {"choices": [{"message": {"content": "```json\n%s\n```" % _dumps(items),
                                     "annotations": [1, 2, 3]}}],
            "usage": {"cost": cost}}

    class _R(object):
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return body

    S.httpx = types.SimpleNamespace(post=lambda *a, **k: _R())


def _dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


def test_json_array_survives_prose_and_fences():
    assert S._parse_json_array('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert S._parse_json_array('Поиск ничего не дал.\n\n```json\n[]\n```') == []
    assert S._parse_json_array("нет никакого json") == []
    assert S._parse_json_array('{"not": "an array"}') == []


def test_foreign_platforms_are_dropped():
    """Probe 26.07 returned eoz.kz, tenderplus.kz and a Russian service for a naive
    query. The .uz gate is what stands between that and Daniyar's Monday report."""
    _stub_store({})
    S._known_hosts = lambda: set()
    S._probe = lambda url: {"alive": True, "status": 200, "relevant": True, "has_print": True}
    _stub_model([{"name": "ЕОЗ", "url": "https://eoz.kz/", "why": "x"},
                 {"name": "Контур", "url": "https://zakupki-kontur.ru/", "why": "x"},
                 {"name": "Новая", "url": "https://newplat.uz/", "why": "x"}])
    out = S.discover(dry=False)
    assert out["kept"] == 1, out
    assert [c["url"] for c in S._load_candidates()] == ["https://newplat.uz/"]


def test_a_model_claim_is_not_evidence():
    """A .uz host that fails the live probe never becomes a candidate."""
    _stub_store({})
    S._known_hosts = lambda: set()
    S._probe = lambda url: {"alive": False, "status": 0, "relevant": False, "has_print": False}
    _stub_model([{"name": "Призрак", "url": "https://ghost.uz/", "why": "x"}])
    assert S.discover(dry=False)["kept"] == 0
    assert S._load_candidates() == []


def test_already_crawled_host_is_not_reproposed():
    _stub_store({})
    S._known_hosts = lambda: {"xarid.uzex.uz"}
    S._probe = lambda url: {"alive": True, "status": 200, "relevant": True, "has_print": True}
    _stub_model([{"name": "Xarid", "url": "https://xarid.uzex.uz/", "why": "x"}])
    assert S.discover(dry=False)["kept"] == 0


def test_discovery_merges_and_never_clobbers():
    _stub_store({S.CAND_KEY: {"items": [{"name": "seed-cand", "url": "https://augz.uz/",
                                         "kind": "aggregator"}]}})
    S._known_hosts = lambda: set()
    S._probe = lambda url: {"alive": True, "status": 200, "relevant": True, "has_print": True}
    _stub_model([{"name": "Новая", "url": "https://newplat.uz/", "why": "x"}])
    S.discover(dry=False)
    names = [c["name"] for c in S._load_candidates()]
    assert "seed-cand" in names and "Новая" in names, names


def test_scan_carries_discoveries_across():
    """scan() rebuilds from SEED and overwrites — a discovery must survive that."""
    store = _stub_store({S.CAND_KEY: {"items": [
        {"name": "Найдена", "url": "https://newplat.uz/", "kind": "discovered"}]}})
    S._known_hosts = lambda: set()
    S._probe = lambda url: {"alive": True, "status": 200, "relevant": True,
                            "has_print": False, "final_host": "etender.uzex.uz"}
    S.scan(dry=False)
    assert "Найдена" in [c["name"] for c in S._load_candidates()], store


def test_report_flags_a_discovery_that_stopped_running():
    _stub_store({S.CAND_KEY: {"items": []}, S.DISCOVER_META_KEY: {
        "last_run": "2026-01-01", "runs": 3, "cost_usd_total": 0.02, "returned": 0, "kept": 0}})
    assert "не запускалась" in S._fmt_report(), S._fmt_report()
    _stub_store({S.CAND_KEY: {"items": []}})
    assert "ещё ни разу" in S._fmt_report()


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
