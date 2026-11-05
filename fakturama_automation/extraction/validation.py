"""The gate between "we read the image" and "we touch the accounting system".

The source document is deliberately redundant: it prints the line items *and*
the totals they add up to.  Those two statements must agree, so a single
mis-read digit almost always breaks one of the equations below.  Checking them
before any UI action converts OCR imprecision from a correctness risk into an
availability one -- the run stops and asks for a human instead of writing a
plausible-looking wrong invoice into Fakturama.

Every failure raises :class:`ManualReviewRequired`, naming the field and
showing both sides of the comparison.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List

from ..errors import ManualReviewRequired
from ..models import OrderDocument, PaidStatus, money

# Payment methods the brief gives a Fakturama payment-code mapping for.
PAYMENT_CODE_MAP = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}

DEFAULT_TOLERANCE = Decimal("0.01")

STEP = "extract.validate"


def _fail(reason: str, **evidence) -> None:
    raise ManualReviewRequired(STEP, reason, evidence)


def validate_order(
    order: OrderDocument,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> List[str]:
    """Check an extracted order for internal consistency.

    Returns a list of human-readable confirmations (useful in the run log).
    Raises :class:`ManualReviewRequired` on the first problem found.
    """
    checks: List[str] = []

    _check_required_fields(order)
    checks.append("required header fields present")

    _check_items(order, tolerance)
    checks.append(f"{len(order.items)} item line(s) reconcile with qty x price x (1 - discount)")

    _check_totals(order, tolerance)
    checks.append("net, VAT and gross totals reconcile with the item lines")

    _check_payment(order)
    checks.append(f"payment method {order.payment.method!r} is mappable and status is consistent")

    return checks


def _check_required_fields(order: OrderDocument) -> None:
    missing = [
        name
        for name, value in (
            ("external reference", order.external_reference),
            ("company", order.company),
            ("currency", order.currency),
            ("payment method", order.payment.method),
        )
        if not str(value).strip()
    ]
    if missing:
        _fail("required field(s) missing from the extracted document", missing=missing)

    if not order.items:
        _fail("document contains no item lines")

    for address, which in (
        (order.billing_address, "billing"),
        (order.delivery_address, "delivery"),
    ):
        blanks = [
            field
            for field in ("name", "street", "zip_code", "city", "country")
            if not str(getattr(address, field)).strip()
        ]
        if blanks:
            _fail(f"{which} address is incomplete", missing=blanks)


def _check_items(order: OrderDocument, tolerance: Decimal) -> None:
    seen_skus = set()
    for item in order.items:
        label = f"line {item.position} ({item.sku})"

        if item.quantity <= 0:
            _fail(f"{label}: quantity must be positive", quantity=str(item.quantity))
        if item.unit_net < 0:
            _fail(f"{label}: unit net price is negative", unit_net=str(item.unit_net))
        if not (Decimal(0) <= item.discount_pct <= Decimal(100)):
            _fail(f"{label}: discount out of range", discount=str(item.discount_pct))
        if not (Decimal(0) <= item.vat_pct <= Decimal(100)):
            _fail(f"{label}: VAT rate out of range", vat=str(item.vat_pct))

        if item.sku in seen_skus:
            _fail(
                f"{label}: the same SKU appears on more than one line, which the "
                "flow cannot resolve unambiguously",
                sku=item.sku,
            )
        seen_skus.add(item.sku)

        expected = item.computed_line_net
        if abs(expected - item.line_net) > tolerance:
            _fail(
                f"{label}: printed line total does not match quantity x unit net "
                "x (1 - discount)",
                printed=str(item.line_net),
                computed=str(expected),
                quantity=str(item.quantity),
                unit_net=str(item.unit_net),
                discount_pct=str(item.discount_pct),
            )


def _check_totals(order: OrderDocument, tolerance: Decimal) -> None:
    comparisons = (
        ("net total", order.net_total, order.computed_net_total),
        ("VAT total", order.vat_total, order.computed_vat_total),
        ("gross total", order.gross_total, order.computed_gross_total),
    )
    for name, printed, computed in comparisons:
        if abs(money(printed) - computed) > tolerance:
            _fail(
                f"{name} printed on the document does not match the item lines",
                printed=str(printed),
                computed=str(computed),
            )

    if abs(money(order.net_total + order.vat_total) - money(order.gross_total)) > tolerance:
        _fail(
            "gross total is not net + VAT",
            net=str(order.net_total),
            vat=str(order.vat_total),
            gross=str(order.gross_total),
        )


def _check_payment(order: OrderDocument) -> None:
    method_key = order.payment.method.strip().casefold()
    if method_key not in PAYMENT_CODE_MAP:
        _fail(
            "payment method has no known Fakturama payment-code mapping; a human "
            "must decide which code applies",
            method=order.payment.method,
            known=sorted(PAYMENT_CODE_MAP),
        )

    if order.payment.status is PaidStatus.PAID and order.payment.payment_date is None:
        _fail("document is marked PAID but no payment date was extracted")

    if order.payment.status is not PaidStatus.PAID and order.payment.payment_date:
        _fail(
            "a payment date was extracted for a document that is not PAID",
            status=order.payment.status.value,
            payment_date=order.payment.payment_date.isoformat(),
        )


def payment_code_for(method: str) -> str:
    """Fakturama payment-code dropdown value for an extracted method."""
    try:
        return PAYMENT_CODE_MAP[method.strip().casefold()]
    except KeyError:
        raise ManualReviewRequired(
            STEP,
            "no Fakturama payment code is defined for this payment method",
            {"method": method, "known": sorted(PAYMENT_CODE_MAP)},
        ) from None
