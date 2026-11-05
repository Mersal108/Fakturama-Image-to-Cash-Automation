"""Typed representation of an order document.

Every monetary value is a :class:`decimal.Decimal`.  Floats are never used for
money: ``0.1 + 0.2 != 0.3`` in binary floating point, and after a few
multiplications the computed totals drift by a cent, which would make the
arithmetic validation in :mod:`fakturama_automation.extraction.validation`
reject perfectly good documents.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import List, Optional

CENTS = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Round a Decimal to 2 places using banker's-free half-up rounding."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


class PaidStatus(str, Enum):
    PAID = "PAID"
    UNPAID = "UNPAID"

    @classmethod
    def parse(cls, raw: str) -> "PaidStatus":
        token = (raw or "").strip().upper()
        if token in ("PAID", "BEZAHLT"):
            return cls.PAID
        if token in ("UNPAID", "OPEN", "OFFEN", "NOT PAID"):
            return cls.UNPAID
        raise ValueError(f"unrecognised paid status: {raw!r}")


@dataclass(frozen=True)
class Address:
    """A postal address as printed on the source document."""

    name: str
    street: str
    zip_code: str
    city: str
    country: str

    def same_place_as(self, other: "Address") -> bool:
        """True when two addresses are the same physical destination.

        Used for the brief's rule: when billing and delivery are identical,
        give the single Main address both roles instead of creating a second
        address record.
        """
        def norm(value: str) -> str:
            return " ".join(value.split()).casefold()

        return (
            norm(self.name) == norm(other.name)
            and norm(self.street) == norm(other.street)
            and norm(self.zip_code) == norm(other.zip_code)
            and norm(self.city) == norm(other.city)
            and norm(self.country) == norm(other.country)
        )


@dataclass(frozen=True)
class Contact:
    first_name: str
    last_name: str
    email: str = ""
    phone: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass(frozen=True)
class PaymentInfo:
    method: str                       # exact text from the image, e.g. "Bank Transfer"
    status: PaidStatus
    payment_date: Optional[_dt.date] = None


@dataclass(frozen=True)
class LineItem:
    """One row of the items table."""

    position: int
    sku: str
    description: str
    quantity: Decimal
    unit: str
    unit_net: Decimal
    discount_pct: Decimal
    vat_pct: Decimal
    line_net: Decimal                 # the total printed on the source document

    @property
    def computed_line_net(self) -> Decimal:
        """quantity x unit net x (1 - discount)  -- what the line *should* total."""
        factor = Decimal(1) - (self.discount_pct / Decimal(100))
        return money(self.quantity * self.unit_net * factor)

    @property
    def product_gross_price(self) -> Decimal:
        """Price (gross) for the Product master record.

        Brief 3.9: ``unit net x (1 + VAT/100)``, rounded to two decimals.  The
        transaction-line discount is deliberately NOT applied -- it belongs to
        this order, not to the product's catalogue price.
        """
        return money(self.unit_net * (Decimal(1) + self.vat_pct / Decimal(100)))

    @property
    def vat_name(self) -> str:
        """Fakturama VAT record name, e.g. ``VAT 19%``  (brief 3.4-3.6)."""
        return f"VAT {format_percent(self.vat_pct)}%"


def format_percent(value: Decimal) -> str:
    """Render a percentage without trailing zeros: 19 -> '19', 7.5 -> '7.5'.

    Formatted with ``:f`` rather than ``str``.  ``Decimal('10').normalize()``
    is ``Decimal('1E+1')``, and ``str`` renders that as '1E+1' -- which is
    both unreadable in a manual-review message and, since this is the value
    typed into the Discount cell, something no number field would accept.
    """
    normalised = value.normalize()
    if normalised == normalised.to_integral_value():
        return f"{normalised.to_integral_value():f}"
    return f"{normalised:f}"


@dataclass(frozen=True)
class OrderDocument:
    """Everything the automation needs, extracted from a single image."""

    external_reference: str
    order_date: _dt.date
    customer_id: str
    currency: str

    company: str
    alias: str
    contact: Contact
    billing_address: Address
    delivery_address: Address

    payment: PaymentInfo
    items: List[LineItem] = field(default_factory=list)

    net_total: Decimal = Decimal("0.00")
    vat_total: Decimal = Decimal("0.00")
    gross_total: Decimal = Decimal("0.00")

    @property
    def delivery_differs(self) -> bool:
        return not self.billing_address.same_place_as(self.delivery_address)

    @property
    def computed_net_total(self) -> Decimal:
        return money(sum((i.line_net for i in self.items), Decimal(0)))

    @property
    def computed_vat_total(self) -> Decimal:
        total = Decimal(0)
        for item in self.items:
            total += item.line_net * item.vat_pct / Decimal(100)
        return money(total)

    @property
    def computed_gross_total(self) -> Decimal:
        return money(self.computed_net_total + self.computed_vat_total)

    @property
    def distinct_vat_rates(self) -> List[Decimal]:
        seen: List[Decimal] = []
        for item in self.items:
            if item.vat_pct not in seen:
                seen.append(item.vat_pct)
        return seen

    def to_dict(self) -> dict:
        """JSON-friendly view, used for run artifacts and tests."""
        return {
            "external_reference": self.external_reference,
            "order_date": self.order_date.isoformat(),
            "customer_id": self.customer_id,
            "currency": self.currency,
            "company": self.company,
            "alias": self.alias,
            "contact": {
                "first_name": self.contact.first_name,
                "last_name": self.contact.last_name,
                "email": self.contact.email,
                "phone": self.contact.phone,
            },
            "billing_address": vars(self.billing_address),
            "delivery_address": vars(self.delivery_address),
            "payment": {
                "method": self.payment.method,
                "status": self.payment.status.value,
                "payment_date": (
                    self.payment.payment_date.isoformat()
                    if self.payment.payment_date
                    else None
                ),
            },
            "items": [
                {
                    "position": i.position,
                    "sku": i.sku,
                    "description": i.description,
                    "quantity": str(i.quantity),
                    "unit": i.unit,
                    "unit_net": str(i.unit_net),
                    "discount_pct": str(i.discount_pct),
                    "vat_pct": str(i.vat_pct),
                    "line_net": str(i.line_net),
                    "product_gross_price": str(i.product_gross_price),
                    "vat_name": i.vat_name,
                }
                for i in self.items
            ],
            "net_total": str(self.net_total),
            "vat_total": str(self.vat_total),
            "gross_total": str(self.gross_total),
        }
