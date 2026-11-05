"""End-to-end extraction against the real sample image.

This runs the actual OCR engine, so it is the test that would catch a
regression in preprocessing, column detection or cell re-reading.  It is
skipped automatically where Windows OCR is unavailable.
"""

import datetime as _dt
from decimal import Decimal

import pytest

from fakturama_automation.extraction.parser import extract_order
from fakturama_automation.extraction.validation import validate_order

from .conftest import SAMPLE_IMAGE, needs_ocr


@pytest.fixture(scope="module")
def extracted():
    return extract_order(SAMPLE_IMAGE)


@needs_ocr
class TestSampleImage:
    def test_header_fields(self, extracted):
        order = extracted.order
        assert order.external_reference == "WEB-2026-0714-A17"
        assert order.order_date == _dt.date(2026, 7, 14)
        assert order.customer_id == "CUST-1007"
        assert order.currency == "EUR"

    def test_customer_and_contact(self, extracted):
        order = extracted.order
        assert order.company == "Northstar Office GmbH"
        assert order.alias == "NORTHSTAR-BERLIN"
        assert order.contact.first_name == "Marta"
        assert order.contact.last_name == "Klein"
        assert order.contact.email == "marta.klein@example.test"
        assert order.contact.phone == "+49 30 5550 1420"

    def test_billing_address(self, extracted):
        address = extracted.order.billing_address
        assert address.name == "Northstar Office GmbH"
        assert address.street == "Friedrichstrasse 88"
        assert address.zip_code == "10117"
        assert address.city == "Berlin"
        assert address.country == "Germany"

    def test_delivery_address_is_read_separately(self, extracted):
        address = extracted.order.delivery_address
        assert address.name == "Northstar Office Warehouse"
        assert address.street == "Beusselstrasse 44"
        assert address.zip_code == "10553"
        assert extracted.order.delivery_differs

    def test_payment(self, extracted):
        payment = extracted.order.payment
        assert payment.method == "Bank Transfer"
        assert payment.status.value == "PAID"
        assert payment.payment_date == _dt.date(2026, 7, 18)

    def test_both_item_lines(self, extracted):
        items = extracted.order.items
        assert len(items) == 2

        chair, mat = items
        assert (chair.position, chair.sku) == (1, "CHR-ERG-01")
        assert chair.description == "Ergonomic Desk Chair"
        assert chair.quantity == Decimal("2")
        assert chair.unit_net == Decimal("250.00")
        assert chair.discount_pct == Decimal("10")
        assert chair.vat_pct == Decimal("19")
        assert chair.line_net == Decimal("450.00")

        assert (mat.position, mat.sku) == (2, "MAT-DESK-02")
        assert mat.description == "Anti-Fatigue Desk Mat"
        assert mat.quantity == Decimal("3")
        assert mat.unit_net == Decimal("40.00")
        assert mat.discount_pct == Decimal("0")
        assert mat.line_net == Decimal("120.00")

    def test_totals(self, extracted):
        order = extracted.order
        assert order.net_total == Decimal("570.00")
        assert order.vat_total == Decimal("108.30")
        assert order.gross_total == Decimal("678.30")

    def test_the_extracted_order_passes_validation(self, extracted):
        assert validate_order(extracted.order)

    def test_the_sku_correction_is_reported_not_hidden(self, extracted):
        """The one OCR failure the engine cannot fix must be visible."""
        assert extracted.sku_corrections == [
            {"before": "CHR-ERG-OI", "after": "CHR-ERG-01"}
        ]
        assert any("CHR-ERG-OI" in note for note in extracted.notes)

    def test_derived_product_prices(self, extracted):
        chair, mat = extracted.order.items
        assert chair.product_gross_price == Decimal("297.50")
        assert mat.product_gross_price == Decimal("47.60")
        assert chair.vat_name == "VAT 19%"
