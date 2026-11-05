import datetime as _dt
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fakturama_automation.models import (  # noqa: E402
    Address,
    Contact,
    LineItem,
    OrderDocument,
    PaidStatus,
    PaymentInfo,
)

SAMPLE_IMAGE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "samples", "order_sample.png")
)


def windows_ocr_available() -> bool:
    try:
        import winrt.windows.media.ocr as ocr

        return ocr.OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception:  # noqa: BLE001
        return False


needs_ocr = pytest.mark.skipif(
    not windows_ocr_available(),
    reason="Windows.Media.Ocr is unavailable on this machine",
)


@pytest.fixture
def sample_order() -> OrderDocument:
    """The ground truth of samples/order_sample.png, written out by hand."""
    return OrderDocument(
        external_reference="WEB-2026-0714-A17",
        order_date=_dt.date(2026, 7, 14),
        customer_id="CUST-1007",
        currency="EUR",
        company="Northstar Office GmbH",
        alias="NORTHSTAR-BERLIN",
        contact=Contact("Marta", "Klein", "marta.klein@example.test",
                        "+49 30 5550 1420"),
        billing_address=Address("Northstar Office GmbH", "Friedrichstrasse 88",
                                "10117", "Berlin", "Germany"),
        delivery_address=Address("Northstar Office Warehouse", "Beusselstrasse 44",
                                 "10553", "Berlin", "Germany"),
        payment=PaymentInfo("Bank Transfer", PaidStatus.PAID, _dt.date(2026, 7, 18)),
        items=[
            LineItem(1, "CHR-ERG-01", "Ergonomic Desk Chair", Decimal("2"), "pcs",
                     Decimal("250.00"), Decimal("10"), Decimal("19"),
                     Decimal("450.00")),
            LineItem(2, "MAT-DESK-02", "Anti-Fatigue Desk Mat", Decimal("3"), "pcs",
                     Decimal("40.00"), Decimal("0"), Decimal("19"),
                     Decimal("120.00")),
        ],
        net_total=Decimal("570.00"),
        vat_total=Decimal("108.30"),
        gross_total=Decimal("678.30"),
    )
