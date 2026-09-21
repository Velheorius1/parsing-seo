"""Контракт cache-first collector: границы, дубли, ошибки и completeness."""
import sys

from crawler.core.competitor_audit import collect_pages, page_body


def test_deals_uses_one_based_exclusive_upper_bound():
    assert page_body("deals", 0, 500) == {"From": 1, "To": 501, "System_Id": 0}
    assert page_body("deals", 1, 500) == {"From": 501, "To": 1001, "System_Id": 0}


def test_direct_pages_can_overlap_without_losing_unique_rows():
    pages = [
        [{"id": 10, "provider_inn": "304788646"}, {"id": 11, "provider_inn": "1"}],
        [{"id": 11, "provider_inn": "1"}, {"id": 12, "provider_inn": "304788646"}],
        [],
    ]
    result = collect_pages(lambda index: pages[index], "direct", max_pages=5,
                           target_inns={"304788646"})

    assert result["completion"] == "archive_end"
    assert result["unique_row_count"] == 3
    assert result["unique_business_id_count"] == 3
    assert result["unique_business_ids"] == ["10", "11", "12"]
    assert [row["id"] for row in result["matches"]] == [10, 12]


def test_repeated_page_is_incomplete_not_zero_results():
    page = [{"id": 10, "provider_inn": "304788646"}]
    result = collect_pages(lambda index: page, "direct", max_pages=5,
                           target_inns={"304788646"})

    assert result["completion"] == "repeated_page"
    assert result["complete"] is False
    assert result["unique_row_count"] == 1


def test_fetch_error_does_not_claim_empty_archive():
    def fetch(index):
        if index == 0:
            return [{"id": 10, "provider_inn": "304788646"}]
        raise RuntimeError("timeout")

    result = collect_pages(fetch, "direct", max_pages=5, target_inns={"304788646"})

    assert result["completion"] == "error"
    assert result["complete"] is False
    assert result["error"] == "timeout"


def test_page_cap_does_not_advance_complete_watermark():
    result = collect_pages(lambda _index: [{"id": 10, "provider_inn": "304788646"}],
                           "direct", max_pages=1, target_inns={"304788646"})

    assert result["completion"] == "page_cap"
    assert result["complete"] is False


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
