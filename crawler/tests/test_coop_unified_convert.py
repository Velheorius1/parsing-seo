"""Unit tests for the coop→notifier unification producer side (2026-07-22).

Covers the converter (`_row_to_raw_tender`) and prefilter (`_prefilter_rows`) in
scripts/fetch_cooperation.py — the seam through which coop rows enter the shared
pipeline. Locks in the known traps:
- jsonb extra_info carries native types (int quantity, bool certificate, None) while
  RawTender wants Dict[str, str] (crash class fixed in investigator.py, af1c155);
- organization can be None;
- contracts carry message_type='info' and the converter must PRESERVE it (the shared
  ALERT_TYPES stage drops them deliberately — masking it would resurrect contract noise);
- extra_info['tnved'] falls back to search_text (feeds the notifier tnved-scope hook
  that keeps the 100-alerts/30d include channel alive);
- prefilter drops TNVED/ENKT-exclude and reject-titles but NEVER force-passes.

Run: python3 -m crawler.tests.test_coop_unified_convert   (exit 1 on any failure)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import fetch_cooperation as fc  # noqa: E402


def _lot_row(**over):
    row = {
        "external_id": "coop-lot-777",
        "title": "Коробка гофрированная для упаковки",
        "organization": None,
        "price": 25_000_000,
        "currency": "UZS",
        "deadline": "2026-08-01",
        "source": "Cooperation.uz Лоты",
        "source_url": "https://cooperation.uz/lot/777",
        "search_text": "Коробка гофрированная ТН ВЭД 4819100000",
        "message_type": "tender",
        "status": "active",
        "extra_info": {"quantity": 500, "certificate": True, "min_part": None,
                       "unit_price": 50000, "offer": "OF-1"},
    }
    row.update(over)
    return row


def test_converter_coerces_extra_info_and_none_org():
    t = fc._row_to_raw_tender(_lot_row())
    assert t.id == "coop-lot-777" and t.external_id == "coop-lot-777"
    assert t.organization == ""                       # None → '' (RawTender requires str)
    for v in t.extra_info.values():
        assert isinstance(v, str), t.extra_info       # int/bool/None all coerced
    assert t.extra_info["quantity"] == "500"
    assert t.extra_info["certificate"] == "True"
    assert t.extra_info["min_part"] == ""             # None → '' not dropped silently
    assert t.price == 25_000_000 and t.source == "Cooperation.uz Лоты"


def test_converter_preserves_contract_info_type():
    t = fc._row_to_raw_tender(_lot_row(message_type="info", status="closed",
                                       source="Cooperation.uz Контракты"))
    assert t.message_type == "info"                   # NOT masked to 'tender'
    assert t.status == "closed"


def test_converter_tnved_fallback_from_search_text():
    row = _lot_row(extra_info={"quantity": 1})        # no tnved key
    t = fc._row_to_raw_tender(row)
    assert t.extra_info.get("tnved", "").startswith("4819"), t.extra_info


def test_prefilter_drops_excludes_keeps_print():
    rows = [
        _lot_row(),                                                        # clean print row
        _lot_row(external_id="x1", title="Бумага туалетная",
                 search_text="Бумага туалетная ТН ВЭД 4818100000"),        # TNVED-exclude
        _lot_row(external_id="x2", title="Салфетки бумажные",
                 search_text="Салфетки бумажные ЕНКТ 17.22.11.100"),       # ENKT-exclude (sanitary)
        _lot_row(external_id="x3", title="Книги печатные разные",
                 search_text="Книги печатные"),                            # reject-title
    ]
    kept = fc._prefilter_rows(rows, "Lots")
    assert [r["external_id"] for r in kept] == ["coop-lot-777"], kept


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
