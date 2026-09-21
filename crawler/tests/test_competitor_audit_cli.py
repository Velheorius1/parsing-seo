"""Cache-first CLI не должен обращаться к сети для уже сохранённых страниц."""
import gzip
import json
import sys
import tempfile
from pathlib import Path

from crawler.scripts.competitor_audit import collect_cache


def test_collect_cache_writes_receipts_and_keeps_exact_inn_matches():
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = Path(temp_dir) / "pages"
        cache.mkdir()
        with gzip.open(str(cache / "1.json.gz"), "wt", encoding="utf-8") as handle:
            json.dump([
                {"id": 1, "provider_inn": "304788646", "contract_sum": 25000000,
                 "currency_name": "UZS", "date_ini": "09/01/2026 10:00:00", "total_count": 2},
                {"id": 2, "provider_inn": "0", "contract_sum": 1, "total_count": 2},
            ], handle)
        with gzip.open(str(cache / "2.json.gz"), "wt", encoding="utf-8") as handle:
            json.dump([], handle)

        manifest = collect_cache("direct", cache, {"304788646"})

    assert manifest["complete"] is True
    assert manifest["completion"] == "archive_end"
    assert manifest["unique_row_count"] == 2
    assert [row["id"] for row in manifest["matches"]] == [1]
    assert manifest["receipts"][0]["sha256"]
    assert manifest["receipts"][0]["body"] == {"from": 1, "to": 500}


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except (AssertionError, ImportError) as exc:
            print("FAIL", test.__name__, "-", str(exc)[:140])
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
