import sys
from datetime import date

from crawler.scripts.collect_ebirja_contract_api import collect_source, normalize


def test_normalize_keeps_public_winner_and_nested_tender_subject():
    row = normalize("tender", {"id": 7, "number": "XT1", "created_at": "2026-09-21 12:00:00",
                                "price": 25000000, "currency": "000", "producer": {"title": "PRINTUZ MCHJ"},
                                "customer": {"title": "Buyer"}, "tender": {"title": "Печать", "lot": "L1"}})
    assert row["winner_name"] == "PRINTUZ MCHJ"
    assert row["title"] == "Печать"
    assert row["amount"] == 25000000



class _Resp:
    content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"data": [], "meta": {"totalCount": 0, "pageCount": 0}}}


class _RecordingClient:
    last = None

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url, params=None):
        _RecordingClient.last = params
        return _Resp()


def test_tender_and_selection_send_type_as_array_like_the_site():
    # 24.09: на скалярный `type` API отвечает 422 «Type is invalid.», и обе
    # строки паспорта бирж выпали в collector_error. Сайт шлёт массив.
    for key, expected in (("tender", [1, 3, 5]), ("selection", [2, 4, 6])):
        collect_source(key, date(2026, 9, 1), 100, 1, client_factory=_RecordingClient)
        params = _RecordingClient.last
        assert params.get("type[]") == expected, params
        assert "type" not in params, params


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test(); print("PASS", test.__name__)
        except AssertionError as exc:
            print("FAIL", test.__name__, str(exc)); failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
