"""Regression guards for bounded Cooperation DB id lookups."""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

# The helper is pure with a supplied client. Avoid importing the real DB client
# and its production settings while still exercising the real split behaviour.
_db = types.ModuleType("crawler.core.db")
_db.query_with_retry = lambda fn, **_kwargs: fn()
sys.modules["crawler.core.db"] = _db

import fetch_cooperation as fc  # noqa: E402


class _Response(object):
    def __init__(self, ids):
        self.data = [{"external_id": value} for value in ids]


class _Query(object):
    def __init__(self, existing, max_ids, calls):
        self.existing = existing
        self.max_ids = max_ids
        self.calls = calls
        self.ids = []
        self.alerted = False

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def in_(self, _field, values):
        self.ids = list(values)
        return self

    @property
    def not_(self):
        return self

    def is_(self, _field, _value):
        self.alerted = True
        return self

    def execute(self):
        self.calls.append((tuple(self.ids), self.alerted))
        if len(self.ids) > self.max_ids:
            raise RuntimeError("gateway 504")
        return _Response(value for value in self.ids if value in self.existing)


class _Client(object):
    def __init__(self, existing, max_ids):
        self.existing = set(existing)
        self.max_ids = max_ids
        self.calls = []

    def table(self, _name):
        return _Query(self.existing, self.max_ids, self.calls)


def test_large_failed_batch_is_split_without_losing_existing_ids():
    client = _Client(existing={"id-2", "id-5"}, max_ids=2)
    found = fc._ids_present(client, "Cooperation.uz Лоты",
                            ["id-1", "id-2", "id-3", "id-4", "id-5"])
    assert found == {"id-2", "id-5"}, found
    assert any(len(ids) > 2 for ids, _ in client.calls), client.calls
    # Recursive depth-first traversal makes an internal 3-id branch appear
    # before its leaves; the last query must still be a bounded leaf.
    assert len(client.calls[-1][0]) <= 2, client.calls


def test_alerted_lookup_keeps_its_additional_filter_when_split():
    client = _Client(existing={"id-1"}, max_ids=1)
    found = fc._ids_present(client, "Cooperation.uz Лоты", ["id-1", "id-2"], only_alerted=True)
    assert found == {"id-1"}
    assert any(alerted for _, alerted in client.calls), client.calls


def test_one_id_failure_is_not_silently_treated_as_absent():
    client = _Client(existing=set(), max_ids=0)
    try:
        fc._ids_present(client, "Cooperation.uz Лоты", ["id-1"])
    except RuntimeError as exc:
        assert "504" in str(exc)
    else:
        raise AssertionError("single-id DB failure must remain visible")


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
