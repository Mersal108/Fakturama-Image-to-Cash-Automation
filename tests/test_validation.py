"""The arithmetic gate.

These tests are the evidence for the central safety claim: because the source
document prints both the item lines and the totals they add up to, a single
mis-read digit breaks an equation and the run stops *before* Fakturama is
touched.  Each test tampers with one value the way OCR would.
"""

import dataclasses
from decimal import Decimal

import pytest

from fakturama_automation.errors import ManualReviewRequired
from fakturama_automation.extraction.validation import (
    payment_code_for,
    validate_order,
)
from fakturama_automation.models import PaidStatus


def tamper(order, **changes):
    return dataclasses.replace(order, **changes)


def tamper_item(order, index, **changes):
    items = list(order.items)
    items[index] = dataclasses.replace(items[index], **changes)
    return dataclasses.replace(order, items=items)


class TestHappyPath:
    def test_the_sample_order_passes_every_gate(self, sample_order):
        checks = validate_order(sample_order)
        assert len(checks) == 4

    def test_line_totals_reconcile(self, sample_order):
        chair, mat = sample_order.items
        assert chair.computed_line_net == Decimal("450.00")   # 2 x 250 x 0.90
        assert mat.computed_line_net == Decimal("120.00")     # 3 x 40 x 1.00

    def test_document_totals_reconcile(self, sample_order):
        assert sample_order.computed_net_total == Decimal("570.00")
        assert sample_order.computed_vat_total == Decimal("108.30")
        assert sample_order.computed_gross_total == Decimal("678.30")


class TestCatchesOcrDamage:
    def test_misread_unit_price_is_caught(self, sample_order):
        """250.00 read as 256.00 -- the line total no longer reconciles."""
        bad = tamper_item(sample_order, 0, unit_net=Decimal("256.00"))
        with pytest.raises(ManualReviewRequired, match="line total"):
            validate_order(bad)

    def test_misread_quantity_is_caught(self, sample_order):
        bad = tamper_item(sample_order, 1, quantity=Decimal("8"))
        with pytest.raises(ManualReviewRequired):
            validate_order(bad)

    def test_misread_discount_is_caught(self, sample_order):
        """A dropped '1' turning 10% into 0% changes the line total."""
        bad = tamper_item(sample_order, 0, discount_pct=Decimal("0"))
        with pytest.raises(ManualReviewRequired):
            validate_order(bad)

    def test_misread_net_total_is_caught(self, sample_order):
        bad = tamper(sample_order, net_total=Decimal("510.00"))
        with pytest.raises(ManualReviewRequired, match="net total"):
            validate_order(bad)

    def test_misread_vat_rate_is_caught_through_the_vat_total(self, sample_order):
        bad = tamper_item(sample_order, 0, vat_pct=Decimal("7"))
        with pytest.raises(ManualReviewRequired, match="VAT total"):
            validate_order(bad)

    def test_gross_that_is_not_net_plus_vat_is_caught(self, sample_order):
        bad = tamper(sample_order, gross_total=Decimal("700.00"))
        with pytest.raises(ManualReviewRequired):
            validate_order(bad)

    def test_a_one_cent_rounding_difference_is_tolerated(self, sample_order):
        ok = tamper(sample_order, vat_total=Decimal("108.31"))
        assert validate_order(ok)


class TestBusinessRules:
    def test_unknown_payment_method_stops_the_run(self, sample_order):
        import dataclasses as dc

        payment = dc.replace(sample_order.payment, method="Carrier Pigeon")
        with pytest.raises(ManualReviewRequired, match="payment-code"):
            validate_order(dc.replace(sample_order, payment=payment))

    def test_paid_without_a_date_stops_the_run(self, sample_order):
        import dataclasses as dc

        payment = dc.replace(sample_order.payment, payment_date=None)
        with pytest.raises(ManualReviewRequired, match="PAID"):
            validate_order(dc.replace(sample_order, payment=payment))

    def test_unpaid_with_a_date_stops_the_run(self, sample_order):
        import dataclasses as dc

        payment = dc.replace(sample_order.payment, status=PaidStatus.UNPAID)
        with pytest.raises(ManualReviewRequired):
            validate_order(dc.replace(sample_order, payment=payment))

    def test_duplicate_skus_stop_the_run(self, sample_order):
        bad = tamper_item(sample_order, 1, sku="CHR-ERG-01")
        with pytest.raises(ManualReviewRequired, match="same SKU"):
            validate_order(bad)

    def test_incomplete_address_stops_the_run(self, sample_order):
        import dataclasses as dc

        address = dc.replace(sample_order.billing_address, city="")
        with pytest.raises(ManualReviewRequired, match="address"):
            validate_order(dc.replace(sample_order, billing_address=address))

    def test_payment_code_mapping_follows_the_brief(self):
        assert payment_code_for("Bank Transfer") == "Credit transfer"
        assert payment_code_for("Credit Card") == "Credit card"
        assert payment_code_for("SEPA Direct Debit") == "SEPA direct debit"
        assert payment_code_for("bank transfer") == "Credit transfer"

    def test_payment_code_mapping_refuses_the_unknown(self):
        with pytest.raises(ManualReviewRequired):
            payment_code_for("Cheque")
