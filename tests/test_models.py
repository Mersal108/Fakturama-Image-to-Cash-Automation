"""Derived values the Fakturama flow depends on."""

from decimal import Decimal

import pytest

from fakturama_automation.models import Address, LineItem, format_percent


def item(**overrides) -> LineItem:
    base = dict(
        position=1, sku="X-1", description="thing", quantity=Decimal("2"),
        unit="pcs", unit_net=Decimal("250.00"), discount_pct=Decimal("10"),
        vat_pct=Decimal("19"), line_net=Decimal("450.00"),
    )
    base.update(overrides)
    return LineItem(**base)


class TestProductGrossPrice:
    def test_excludes_the_transaction_line_discount(self):
        """Brief 3.9: the master price is unit net x (1 + VAT), undiscounted."""
        assert item().product_gross_price == Decimal("297.50")

    def test_rounds_half_up_to_two_decimals(self):
        assert item(unit_net=Decimal("40.00")).product_gross_price == Decimal("47.60")
        assert item(unit_net=Decimal("9.99"),
                    vat_pct=Decimal("7")).product_gross_price == Decimal("10.69")

    def test_zero_vat_leaves_the_price_unchanged(self):
        assert item(vat_pct=Decimal("0")).product_gross_price == Decimal("250.00")


class TestComputedLineNet:
    def test_applies_the_discount(self):
        assert item().computed_line_net == Decimal("450.00")

    def test_without_discount(self):
        assert item(quantity=Decimal("3"), unit_net=Decimal("40.00"),
                    discount_pct=Decimal("0")).computed_line_net == Decimal("120.00")


class TestVatName:
    @pytest.mark.parametrize(
        "percent,expected",
        [("19", "VAT 19%"), ("7", "VAT 7%"), ("7.5", "VAT 7.5%"),
         ("19.00", "VAT 19%"), ("0", "VAT 0%")],
    )
    def test_matches_the_fakturama_naming_rule(self, percent, expected):
        assert item(vat_pct=Decimal(percent)).vat_name == expected

    def test_format_percent_drops_trailing_zeros(self):
        assert format_percent(Decimal("19.00")) == "19"
        assert format_percent(Decimal("7.5")) == "7.5"

    def test_format_percent_never_uses_scientific_notation(self):
        """A round ten normalises to Decimal('1E+1').

        This value is typed into the Discount cell and printed in
        manual-review messages, so '1E+1' would be both unenterable and
        unreadable.  A live run reported "applies a 1E+1% discount".
        """
        assert format_percent(Decimal("10")) == "10"
        assert format_percent(Decimal("10.00")) == "10"
        assert format_percent(Decimal("100")) == "100"
        assert format_percent(Decimal("1000")) == "1000"

    def test_format_percent_keeps_small_and_zero_values(self):
        assert format_percent(Decimal("0")) == "0"
        assert format_percent(Decimal("0.5")) == "0.5"
        assert format_percent(Decimal("2.50")) == "2.5"


class TestAddressComparison:
    def test_identical_addresses_share_one_record(self):
        a = Address("ACME", "Main St 1", "10117", "Berlin", "Germany")
        b = Address("ACME", "Main  St 1", "10117", "berlin", "GERMANY")
        assert a.same_place_as(b)

    def test_different_street_is_a_different_place(self):
        a = Address("ACME", "Main St 1", "10117", "Berlin", "Germany")
        b = Address("ACME", "Other St 2", "10117", "Berlin", "Germany")
        assert not a.same_place_as(b)

    def test_sample_order_needs_a_separate_delivery_address(self, sample_order):
        assert sample_order.delivery_differs
