"""Stage 5 of the brief: the linked Invoice and its payment status.

The Invoice is created from the saved Order's *Create a follow-up document*
area, not from the top toolbar.  That distinction matters: only the follow-up
action keeps the Order-to-Invoice relationship, and the toolbar button would
silently produce an unlinked document.
"""

from __future__ import annotations

from typing import Any, List

from ..logging_setup import get_logger
from ..models import PaidStatus
from ..uia import selectors as sel
from ..uia.actions import click, read_value, select_combo, set_checkbox, set_text, values_match
from ..uia.locators import normalize, rect_of, safe_name
from ..uia.waits import wait_until
from .context import RunContext
from .widgets import icons_near, set_date_field

log = get_logger("invoice")


def create_linked_invoice(ctx: RunContext) -> None:
    """4.6-4.7  Click Invoice inside 'Create a follow-up document'."""
    control = _follow_up_invoice_control(ctx)
    click(control, description="Create a follow-up document > Invoice")

    wait_until(
        lambda: _invoice_editor_ready(ctx),
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        description="the linked New Invoice editor",
    )
    log.info("linked Invoice editor open")


def _follow_up_invoice_control(ctx: RunContext):
    """The Invoice button in the follow-up area -- never the toolbar one."""
    labels: List[Any] = []
    for caption in sel.FOLLOW_UP_AREA:
        labels.extend(ctx.app.finder.find_labels(caption, exact=False))

    if labels:
        anchor = rect_of(labels[0])
        buttons = ctx.app.finder.descendants(control_type="Button")
        near = []
        for button in buttons:
            if not any(normalize(safe_name(button)) == normalize(name)
                       for name in sel.FOLLOW_UP_INVOICE):
                continue
            rect = rect_of(button)
            dy = rect.top - anchor.bottom
            if -40 <= dy <= 220:
                near.append((dy, rect.left, button))
        if near:
            near.sort()
            return near[0][2]

        icons = icons_near(ctx.app, sel.FOLLOW_UP_AREA, max_distance=260)
        if len(icons) >= 2:
            # Confirmation, Invoice, Delivery, Proforma -- Invoice is second.
            return icons[1]

    ctx.stop(
        "could not find the Invoice action inside 'Create a follow-up document'; "
        "refusing to fall back to the toolbar Invoice button because that "
        "would not preserve the link to the Order"
    )


def _invoice_editor_ready(ctx: RunContext) -> bool:
    try:
        ctx.app.by_label_any(sel.CHK_PAID, control_types=("CheckBox",), timeout=2.0)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        ctx.app.by_label_any(sel.LBL_CUSTOMER_REF, timeout=2.0)
        return True
    except Exception:  # noqa: BLE001
        return False


def confirm_copied_fields(ctx: RunContext) -> None:
    """5.1  The Invoice must carry the Order's reference and totals."""
    order = ctx.order

    try:
        custref = read_value(ctx.app.by_label_any(sel.LBL_CUSTOMER_REF, timeout=5.0))
    except Exception as exc:  # noqa: BLE001
        ctx.stop("could not read Cust.Ref. on the Invoice", error=str(exc))

    if not values_match(custref, order.external_reference):
        ctx.stop(
            "the Invoice's Cust.Ref. was not copied from the Order",
            invoice_shows=custref, expected=order.external_reference,
        )
    ctx.record("invoice.cust_ref", custref)

    for labels, expected, name in (
        (sel.LBL_TOTAL_NET, order.net_total, "total net"),
        (sel.LBL_TOTAL, order.gross_total, "gross total"),
    ):
        try:
            shown = read_value(ctx.app.by_label_any(
                labels, control_types=("Edit", "Text"), timeout=4.0))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read Invoice %s (%s)", name, exc)
            continue
        if not values_match(shown, str(expected), numeric=True):
            ctx.stop(
                f"the Invoice's {name} does not match the Order",
                invoice_shows=shown, expected=str(expected),
            )
        ctx.record(f"invoice.{name.replace(' ', '_')}", shown)


def apply_payment(ctx: RunContext) -> None:
    """5.2-5.3  Payment method, and the paid state exactly as the image says."""
    order = ctx.order
    _confirm_payment_method(ctx)

    if order.payment.status is not PaidStatus.PAID:
        _ensure_unpaid(ctx)
        ctx.record("invoice.paid", False)
        return

    checkbox = _paid_checkbox(ctx)
    set_checkbox(checkbox, True, description="Invoice 'paid'")

    _set_payment_date(ctx)
    _set_payment_value(ctx)
    ctx.record("invoice.paid", True)


def _confirm_payment_method(ctx: RunContext) -> None:
    method = ctx.order.payment.method
    combos = ctx.app.finder.descendants(control_type="ComboBox")
    for combo in combos:
        try:
            options = [str(t) for t in combo.texts() if t]
        except Exception:  # noqa: BLE001
            continue
        if any(normalize(option) == normalize(method) for option in options):
            select_combo(combo, method, description="Invoice payment method")
            ctx.record("invoice.payment_method", method)
            return
    ctx.stop(
        "the Invoice does not offer the payment method extracted from the image",
        method=method,
    )


def _paid_checkbox(ctx: RunContext):
    for caption in sel.CHK_PAID:
        hits = ctx.app.finder.descendants(control_type="CheckBox", name=caption)
        if hits:
            return hits[0]
    boxes = ctx.app.finder.descendants(control_type="CheckBox")
    if boxes:
        return boxes[0]
    ctx.stop("could not find the Invoice's 'paid' checkbox")


def _ensure_unpaid(ctx: RunContext) -> None:
    """5.3  Not PAID: leave the box clear and invent neither date nor value."""
    try:
        set_checkbox(_paid_checkbox(ctx), False, description="Invoice 'paid'")
    except Exception as exc:  # noqa: BLE001
        log.debug("could not confirm the paid box is clear (%s)", exc)
    log.info("document is not PAID; no payment date or value entered")


def _set_payment_date(ctx: RunContext) -> None:
    date = ctx.order.payment.payment_date
    if date is None:
        ctx.stop("the document is PAID but no payment date was extracted")

    checkbox = _paid_checkbox(ctx)
    anchor = rect_of(checkbox)
    candidates = []
    for control in ctx.app.finder.descendants(control_type="Edit"):
        rect = rect_of(control)
        if anchor.vertical_overlap(rect) < 0.3 or rect.left < anchor.left:
            continue
        candidates.append((rect.left, control))
    candidates.sort()

    for _x, control in candidates:
        current = read_value(control)
        from .widgets import infer_date_format

        if infer_date_format(current):
            set_date_field(control, date, description="Invoice payment date")
            ctx.record("invoice.payment_date", date.isoformat())
            return

    ctx.stop("could not find the Invoice's payment-date field beside 'paid'")


def _set_payment_value(ctx: RunContext) -> None:
    """5.3  Value is the full invoice total -- never a guess."""
    total = ctx.order.gross_total
    try:
        control = ctx.app.by_label_any(sel.LBL_PAY_VALUE, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        ctx.stop("could not find the Invoice's payment Value field", error=str(exc))

    set_text(control, f"{total:.2f}", description="Invoice payment Value",
             numeric=True)
    ctx.record("invoice.payment_value", f"{total:.2f}")


def save_invoice(ctx: RunContext) -> None:
    """5.4  Click Save once."""
    ctx.app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save (Invoice)",
                      control_type="Button", timeout=ctx.settings.timeout)
    log.info("Invoice saved")
