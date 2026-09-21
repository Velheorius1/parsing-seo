"""Историческая связь не подменяет отсутствие timestamp догадкой о доставке."""
import sys

from crawler.core.competitor_audit import historical_coverage


def test_result_only_row_is_not_timely_delivery():
    coverage = historical_coverage({"procedure_id": "497899"}, [{
        "source_url": "https://etender.uzex.uz/lot/497899", "source": "ETender Сделки (победители)",
        "alert_seq": 4734, "telegram_message_id": 13952, "deadline": None,
    }])
    assert coverage["outcome"] == "late_result_only"
    assert coverage["timely"] is False


def test_active_row_with_message_but_no_sent_time_is_unknown_timing():
    coverage = historical_coverage({"procedure_id": "498104"}, [{
        "source_url": "https://etender.uzex.uz/lot/498104", "source": "ETender UZEX",
        "alert_seq": 4356, "telegram_message_id": 12983, "deadline": "2026-07-10T11:01:50",
    }])
    assert coverage["outcome"] == "unknown"
    assert coverage["reason"] == "message_time_missing"
    assert coverage["delivery"] == "confirmed"


def test_no_snapshot_row_is_unknown_not_confirmed_miss():
    coverage = historical_coverage({"procedure_id": "999999"}, [])
    assert coverage["outcome"] == "unknown"
    assert coverage["reason"] == "no_snapshot_match"
    assert coverage["delivery"] == "unknown"


def test_alert_sequence_without_message_id_is_not_delivery_confirmation():
    coverage = historical_coverage({"procedure_id": "498105"}, [{
        "source_url": "https://etender.uzex.uz/lot/498105", "source": "ETender UZEX",
        "alert_seq": 4357, "telegram_message_id": None, "deadline": "2026-07-10T11:01:50",
    }])
    assert coverage["outcome"] == "unknown"
    assert coverage["delivery"] == "unknown"


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
