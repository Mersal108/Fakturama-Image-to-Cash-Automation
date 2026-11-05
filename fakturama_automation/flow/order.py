"""Stage 1 and 4 of the brief: open, fill and save the Order.

The Order is opened *first* and deliberately stays open for the whole run.
Every master-data detour (debtor, payment method, VAT, product) returns to
this same editor, and the Order's own selectors are what prove the new records
exist.
"""

from __future__ import annotations

from ..logging_setup import get_logger
from ..uia import selectors as sel
from ..uia.actions import read_value, select_combo, set_text, values_match
from ..uia.waits import wait_until
from .context import RunContext
from .widgets import set_date_field

log = get_logger("order")


def open_new_order(ctx: RunContext) -> None:
    """1.3  Click Order in the top toolbar and wait for the New Order editor."""
    app = ctx.app
    app.click_any(sel.TOOLBAR_ORDER, description="toolbar Order button",
                  control_type="Button", timeout=ctx.settings.timeout)

    # The editor is ready once its Cust.Ref. field can be addressed.
    wait_until(
        lambda: _custref_field(ctx),
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        description="the New Order editor",
    )
    proposed_number = _read_number(ctx)
    ctx.record("order.proposed_number", proposed_number)
    log.info("New Order editor open; keeping the proposed number %r", proposed_number)


def _custref_field(ctx: RunContext):
    return ctx.app.by_label_any(sel.LBL_CUSTOMER_REF, timeout=3.0)


def _read_number(ctx: RunContext) -> str:
    """1.4  Read, but never change, the automatically proposed number."""
    try:
        control = ctx.app.by_label_any(sel.LBL_NUMBER, timeout=4.0)
        return read_value(control)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not read the proposed No.: %s", exc)
        return ""


def set_order_header(ctx: RunContext) -> None:
    """1.5-1.7  Date, Cust.Ref. and the net / with-VAT price mode."""
    order = ctx.order

    date_control = ctx.app.by_label_any(sel.LBL_DATE, timeout=ctx.settings.short_timeout)
    set_date_field(date_control, order.order_date, description="Order Date")
    ctx.record("order.date", order.order_date.isoformat())

    custref = _custref_field(ctx)
    set_text(custref, order.external_reference, description="Cust.Ref.")
    ctx.record("order.cust_ref", order.external_reference)

    _set_price_mode(ctx)


def _set_price_mode(ctx: RunContext) -> None:
    """1.7  Document price mode Net, VAT mode left at 'with VAT'."""
    try:
        combos = ctx.app.finder.descendants(control_type="ComboBox")
    except Exception:  # noqa: BLE001
        combos = []

    net_set = False
    for combo in combos:
        options = []
        try:
            options = [str(t) for t in combo.texts() if t]
        except Exception:  # noqa: BLE001
            continue
        normalized = {o.strip().casefold() for o in options}
        if {"net", "gross"} <= normalized or {"netto", "brutto"} <= normalized:
            select_combo(combo, sel.OPT_NET[0], description="document price mode")
            net_set = True
            break

    if not net_set:
        log.warning(
            "could not find a net/gross selector; leaving the document price "
            "mode at its default and relying on the totals check"
        )
        ctx.extraction_notes.append(
            "document price mode could not be set explicitly; totals were "
            "verified against the source instead"
        )
    else:
        ctx.record("order.price_mode", "net")


def confirm_totals(ctx: RunContext) -> None:
    """4.3  Confirm Total Net, VAT and Total match the source document."""
    order = ctx.order
    expectations = (
        (sel.LBL_TOTAL_NET, order.net_total, "total net"),
        (sel.LBL_VAT, order.vat_total, "VAT total"),
        (sel.LBL_TOTAL, order.gross_total, "gross total"),
    )

    for labels, expected, name in expectations:
        try:
            control = ctx.app.by_label_any(
                labels, control_types=("Edit", "Text"), timeout=4.0
            )
            shown = read_value(control)
        except Exception as exc:  # noqa: BLE001
            ctx.stop(
                f"could not read the order's {name} to compare against the source",
                error=str(exc), expected=str(expected),
            )
        if not values_match(shown, str(expected), numeric=True):
            ctx.stop(
                f"order {name} does not match the source document",
                order_shows=shown, source_says=str(expected),
            )
        ctx.record(f"order.{name.replace(' ', '_')}", shown)


def save_order(ctx: RunContext) -> None:
    """4.4  Click the toolbar Save control exactly once."""
    ctx.app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save",
                      control_type="Button", timeout=ctx.settings.timeout)
    # Saving clears Eclipse's dirty marker ('*') from the editor tab title.
    try:
        wait_until(
            lambda: not _editor_is_dirty(ctx),
            timeout=ctx.settings.short_timeout,
            interval=ctx.settings.poll_interval,
            description="the Order editor to leave its modified state",
        )
    except Exception:  # noqa: BLE001
        log.debug("dirty-marker check inconclusive; the Documents list is authoritative")
    log.info("Order saved")


def _editor_is_dirty(ctx: RunContext) -> bool:
    try:
        tabs = ctx.app.finder.descendants(control_type="TabItem")
    except Exception:  # noqa: BLE001
        return False
    from ..uia.locators import safe_name

    return any(safe_name(tab).strip().startswith("*") for tab in tabs)
