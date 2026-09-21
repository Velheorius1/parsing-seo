"""Чистые правила аудита конкурентов: идентичность и денежный порог."""
from decimal import Decimal
import sys

from crawler.core.competitor_audit import (
    above_threshold,
    entity_for_inn,
    load_registry,
    normalize_inn,
)


def test_same_name_different_inn_stays_separate():
    registry = load_registry()
    centris = entity_for_inn(registry, "307491912")
    centris_print = entity_for_inn(registry, "308717019")

    assert centris["name"] == "CENTRIS"
    assert centris_print["name"] == "CENTRIS-PRINT"
    assert centris["inn"] != centris_print["inn"]


def test_exact_inn_does_not_depend_on_company_name_spelling():
    registry = load_registry()

    entity = entity_for_inn(registry, " 205353003 ")

    assert entity["name"] == "KOLORPAK"
    assert entity["inn"] == "205353003"


def test_books_is_unresolved_and_never_inherits_service_inn():
    registry = load_registry()
    books = next(item for item in registry["entities"] if item["name"] == "STANDARD POLIGRAF BOOKS")

    assert books["inn"] is None
    assert entity_for_inn(registry, "207063624")["name"] == "STANDARD POLIGRAF SERVICE"


def test_invalid_or_zero_inn_is_unresolved():
    registry = load_registry()

    assert normalize_inn("0") is None
    assert normalize_inn("000000000") is None
    assert normalize_inn("KOLORPAK") is None
    assert normalize_inn("205049902") == "205049902"
    assert entity_for_inn(registry, "0") is None
    assert entity_for_inn(registry, "KOLORPAK") is None


def test_threshold_is_strict_and_currency_aware():
    assert above_threshold("20000000", "UZS") is False
    assert above_threshold("20000000.01", "UZS") is True
    assert above_threshold(None, "UZS") is None
    assert above_threshold("30000000", "USD") is None
    assert above_threshold(Decimal("30000000"), "Сум") is True


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
