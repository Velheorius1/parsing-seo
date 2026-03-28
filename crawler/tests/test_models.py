"""Tests for crawler.core.models — Pydantic validation."""

import pytest
from datetime import datetime

from crawler.core.models import (
    AdapterType,
    FieldMap,
    HtmlSelectors,
    PaginationConfig,
    RawTender,
    SourceConfig,
)


# ── RawTender ───────────────────────────────────────────────────


class TestRawTender:
    def test_minimal_valid(self):
        t = RawTender(
            id="etender-123",
            external_id="123",
            title="Test",
            organization="ООО Test",
            source="etender",
        )
        assert t.id == "etender-123"
        assert t.price is None
        assert t.currency == "UZS"
        assert t.status == "active"
        assert t.message_type == "tender"
        assert t.categories == []
        assert t.region == ""

    def test_full_fields(self):
        t = RawTender(
            id="xarid-456",
            external_id="456",
            title="Коробки гофро",
            organization="АО Навои",
            price=15000000.0,
            currency="USD",
            deadline="2026-05-15",
            date_start="2026-04-01",
            date_end="2026-05-15",
            region="Tashkent",
            categories=["packaging", "printing"],
            source="xarid.uz",
            source_url="https://xarid.uz/tender/456",
            status="active",
            search_text="коробки гофро 1000 шт для молочной продукции",
            message_type="tender",
        )
        assert t.price == 15000000.0
        assert len(t.categories) == 2
        assert t.region == "Tashkent"

    def test_collected_at_auto(self):
        t = RawTender(
            id="t1", external_id="1", title="T", organization="O", source="s"
        )
        assert isinstance(t.collected_at, datetime)

    def test_optional_winner_fields(self):
        t = RawTender(
            id="t1", external_id="1", title="T", organization="O", source="s",
            winner="ООО Winner",
            winning_price=5000000.0,
            result_date="2026-03-15",
        )
        assert t.winner == "ООО Winner"
        assert t.winning_price == 5000000.0

    def test_message_types(self):
        for msg_type in ("tender", "customer_request", "info"):
            t = RawTender(
                id="t1", external_id="1", title="T", organization="O",
                source="s", message_type=msg_type,
            )
            assert t.message_type == msg_type

    def test_price_zero_is_valid(self):
        t = RawTender(
            id="t1", external_id="1", title="T", organization="O",
            source="s", price=0.0,
        )
        assert t.price == 0.0

    def test_negative_price_accepted(self):
        """Pydantic doesn't constrain price range — adapters handle that."""
        t = RawTender(
            id="t1", external_id="1", title="T", organization="O",
            source="s", price=-100.0,
        )
        assert t.price == -100.0


# ── FieldMap ────────────────────────────────────────────────────


class TestFieldMap:
    def test_defaults(self):
        fm = FieldMap()
        assert fm.title == "title"
        assert fm.organization == "organization"
        assert fm.price == "price"
        assert fm.external_id == "id"

    def test_custom_mapping(self):
        fm = FieldMap(title="name", organization="company", price="amount")
        assert fm.title == "name"

    def test_optional_fields_none(self):
        fm = FieldMap(date_start=None, region=None)
        assert fm.date_start is None


# ── PaginationConfig ────────────────────────────────────────────


class TestPaginationConfig:
    def test_defaults(self):
        pc = PaginationConfig()
        assert pc.type == "offset"
        assert pc.page_size == 100
        assert pc.max_pages == 10
        assert pc.page_start == 0

    def test_cursor_type(self):
        pc = PaginationConfig(type="cursor", param="cursor", page_size=50)
        assert pc.type == "cursor"


# ── SourceConfig ────────────────────────────────────────────────


class TestSourceConfig:
    def test_minimal(self):
        sc = SourceConfig(
            id="test",
            name="Test Source",
            adapter=AdapterType.API,
            url="https://api.example.com",
            id_prefix="test",
        )
        assert sc.enabled is True
        assert sc.method == "GET"
        assert sc.rate_limit == 2.0
        assert sc.timeout == 15

    def test_telegram_adapter(self):
        sc = SourceConfig(
            id="tg-test",
            name="TG Channel",
            adapter=AdapterType.TELEGRAM,
            url="",
            id_prefix="tg-test",
            telegram_channel="@testchannel",
            telegram_limit=200,
        )
        assert sc.adapter == AdapterType.TELEGRAM
        assert sc.telegram_channel == "@testchannel"

    def test_html_adapter_with_selectors(self):
        sc = SourceConfig(
            id="html-test",
            name="HTML Source",
            adapter=AdapterType.HTML,
            url="https://example.com/tenders",
            id_prefix="html",
            html_selectors=HtmlSelectors(
                container=".tender-item",
                title=".title",
                organization=".org",
                price=".price",
            ),
        )
        assert sc.html_selectors is not None
        assert sc.html_selectors.container == ".tender-item"

    def test_disabled_source(self):
        sc = SourceConfig(
            id="disabled",
            name="Disabled",
            adapter=AdapterType.API,
            url="https://api.example.com",
            id_prefix="dis",
            enabled=False,
        )
        assert sc.enabled is False
