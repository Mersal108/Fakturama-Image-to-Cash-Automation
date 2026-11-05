"""Stage 1 and 4 of the brief: open, fill and save the Order.

The Order is opened *first* and deliberately stays open for the whole run.
Every master-data detour (debtor, payment method, VAT, product) returns to
this same editor, and the Order's own selectors are what prove the new records
exist.
"""

from __future__ import annotations

from ..logging_setup import get_logger
from ..uia import selectors as sel
from ..uia.actions import (
    combo_options, read_value, select_combo, set_text, values_match,
)
from ..uia.waits import wait_until
from .context import RunContext
from .widgets import set_date_field

log = get_logger("order")


def open_new_order(ctx: RunContext) -> None:
    """1.3  Click Order in the top toolbar and wait for the New Order editor."""
    app = ctx.app
    app.click_any(sel.TOOLBAR_ORDER, description="toolbar Order button",
                  control_type="Button", timeout=ctx.settings.timeout)

    wait_until(
        lambda: _editor_ready(ctx),
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        description="the New Order editor to finish laying out",
    )
    proposed_number = _read_number(ctx)
    ctx.record("order.proposed_number", proposed_number)
    ctx.order_tab = _active_editor_tab(ctx)
    log.info("New Order editor open (tab %r); keeping the proposed number %r",
             ctx.order_tab, proposed_number)


def _editor_ready(ctx: RunContext) -> bool:
    """True only when the whole header has been laid out and populated.

    Waiting on a single field is not enough.  Fakturama creates the editor's
    widgets progressively, so Cust.Ref. can be addressable while Date does not
    exist yet and the proposed number is still blank -- which looks exactly
    like a missing control and aborts the run.  Readiness therefore means every
    header field this stage is about to touch resolves, *and* the number the
    application proposes has actually arrived.
    """
    try:
        ctx.app.by_label_any(sel.LBL_CUSTOMER_REF, timeout=1.5)
        ctx.app.by_label_any(sel.LBL_DATE, timeout=1.5)
        number = ctx.app.by_label_any(sel.LBL_NUMBER, timeout=1.5)
    except Exception:  # noqa: BLE001
        return False
    # An empty No. means the editor is still initialising: Fakturama fills in
    # the next document number itself, and 1.4 requires leaving it untouched.
    return bool(read_value(number).strip())


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
        # combo_options() drops the list down to read it; an SWT combo reports
        # nothing useful until it does.
        options = combo_options(combo)
        if not options:
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
    """4.3  Confirm Total Net, VAT and Total match the source document.

    This also enforces 4.2 -- order-level Discount stays 0% and Shipping stays
    free -- without touching either control.  The source image supplies no
    order-level discount or shipping, so if Fakturama had applied one the
    gross total would no longer equal the document's, and the comparison below
    would stop the run.  Verifying the outcome beats setting the inputs and
    assuming.
    """
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


def _editor_tabs(ctx):
    """The editor tab strip, which sits above the editor area."""
    from ..uia.locators import rect_of, safe_name

    tabs = []
    try:
        for tab in ctx.app.finder.descendants(control_type="TabItem"):
            name = safe_name(tab)
            if name and rect_of(tab).top < 200:
                tabs.append((name, tab))
    except Exception:  # noqa: BLE001
        return []
    return tabs


def _active_editor_tab(ctx) -> str:
    """Caption of the document editor that is currently in front.

    The selected tab is asked for explicitly.  Several editors are open at
    once by design, and simply taking the first one in tree order returns
    whichever happens to be earliest -- usually the Order -- rather than the
    editor that was just opened.
    """
    from ..uia.locators import normalize

    known = {normalize(n) for n in ("Fakturama", "Documents")}
    candidates = [
        (name.lstrip("*"), tab) for name, tab in _editor_tabs(ctx)
        if normalize(name.lstrip("*")) not in known
    ]
    for name, tab in candidates:
        try:
            if tab.is_selected():
                return name
        except Exception:  # noqa: BLE001
            continue
    return candidates[0][0] if candidates else ""


def activate_editor(ctx, wanted: str, *, description: str = "editor") -> bool:
    """Bring a named document editor back to the front.

    Every master-data detour -- Debtor, payment method, VAT rate, Product --
    opens its own editor *on top of* the one being worked on.  While another
    editor is in front, the covered editor's controls are not visible to UIA
    at all, so looking for them finds nothing and the step fails as though the
    control did not exist.  Returning to an editor is therefore an explicit
    action, not something that follows from never having closed it.
    """
    from ..uia.actions import click
    from ..uia.locators import normalize, safe_name

    target = normalize(wanted)
    if not target:
        return False
    for name, tab in _editor_tabs(ctx):
        plain = normalize(safe_name(tab).lstrip("*"))
        if plain == target or target in plain:
            click(tab, description=f"{description} tab {name!r}")
            return True
    log.debug("no %s tab matching %r; leaving focus where it is",
              description, wanted)
    return False


def activate_order(ctx) -> None:
    """Brief 1.8 / 2.12: return to the Order, which stays open throughout."""
    if not activate_editor(ctx, ctx.order_tab or "new order",
                           description="Order editor"):
        return

    # Confirm the Order really is in front before the caller touches it.
    try:
        wait_until(
            lambda: ctx.app.by_label_any(sel.LBL_CUSTOMER_REF, timeout=1.5),
            timeout=ctx.settings.short_timeout,
            interval=ctx.settings.poll_interval,
            description="the Order editor to come back to the front",
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("could not confirm the Order editor is active: %s", exc)
