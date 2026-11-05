"""Brief 2.10: the Debtor's payment method, created on demand.

The Debtor editor stays open throughout.  Fakturama's *terms of payment* list
is consulted first; a new entry is only created when no exact one exists, and
a conflicting definition stops the run rather than being overwritten.
"""

from __future__ import annotations

from ..extraction.validation import payment_code_for
from ..logging_setup import get_logger
from ..uia import selectors as sel
from ..uia.actions import click, select_combo, set_text
from ..uia.locators import normalize
from ..uia.waits import wait_for_stable
from .context import RunContext
from .widgets import grid_items, icons_near

log = get_logger("payment")


def ensure_payment_method(ctx: RunContext) -> None:
    """Select the exact payment method on the Debtor, creating it if needed."""
    method = ctx.order.payment.method
    from .debtor import _open_tab  # local import avoids a circular dependency

    _open_tab(ctx, sel.TAB_PAYMENT)

    if _try_select_on_debtor(ctx, method):
        ctx.record("debtor.payment_method", method)
        return

    log.info("payment method %r is not available; creating it", method)
    create_payment_method(ctx, method)

    from .debtor import _open_tab as open_tab
    open_tab(ctx, sel.TAB_PAYMENT)
    if not _try_select_on_debtor(ctx, method):
        ctx.stop(
            "the newly created payment method still cannot be selected on the Debtor",
            method=method,
        )
    ctx.record("debtor.payment_method", method)


def _try_select_on_debtor(ctx: RunContext, method: str) -> bool:
    """Is the exact method offered in the Debtor's payment combo?"""
    combos = ctx.app.finder.descendants(control_type="ComboBox")
    target = normalize(method)
    for combo in combos:
        try:
            options = [str(t) for t in combo.texts() if t]
        except Exception:  # noqa: BLE001
            continue
        if any(normalize(option) == target for option in options):
            try:
                select_combo(combo, method, description="Debtor payment method")
                return True
            except Exception as exc:  # noqa: BLE001
                log.debug("combo select failed: %s", exc)
    return False


def create_payment_method(ctx: RunContext, method: str) -> None:
    """2.10.1-2.10.6  Create the payment method from Data > terms of payment."""
    app = ctx.app
    code = payment_code_for(method)

    app.click_any(sel.NAV_TERMS_OF_PAYMENT, description="Data > terms of payment",
                  timeout=ctx.settings.timeout)

    existing = _search_list(ctx, method)
    exact = [cells for _c, cells in existing
             if any(normalize(cell) == normalize(method) for cell in cells)]
    if len(exact) > 1:
        ctx.stop(
            "several payment methods match that name exactly; a human must "
            "decide which one the order means",
            method=method, candidates=[" | ".join(c) for c in exact],
        )
    if exact:
        log.info("payment method %r already exists; reusing it", method)
        return

    _click_new_entry(ctx)

    set_text(app.by_label_any(sel.LBL_PAYMENT_NAME), method, description="payment Name")
    set_text(app.by_label_any(sel.LBL_PAYMENT_DESCRIPTION), method,
             description="payment Description")
    # 2.10.3  Account is deliberately left blank.

    try:
        combo = app.by_label_any(sel.LBL_PAYMENT_CODE, timeout=5.0)
        select_combo(combo, code, description=f"payment code for {method!r}")
    except Exception as exc:  # noqa: BLE001
        ctx.stop(
            "could not set the payment-code dropdown, so the new payment "
            "method would be incomplete",
            method=method, wanted_code=code, error=str(exc),
        )

    for labels, description in (
        (sel.LBL_CASH_DISCOUNT, "Cash discount"),
        (sel.LBL_DISCOUNT_DAYS, "Discount Days"),
        (sel.LBL_NET_DAYS, "Net Days"),
    ):
        try:
            set_text(app.by_label_any(labels, timeout=4.0), "0",
                     description=description, numeric=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not zero %s (%s)", description, exc)

    # 2.10.5  The three text templates stay blank and 'Set as standard' is
    # deliberately not clicked.
    app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save (payment method)",
                  control_type="Button", timeout=ctx.settings.timeout)
    ctx.note_created("payments", f"{method} -> {code}")
    app.screenshot("2-payment-method", f"payment method {method} ({code})")


def _search_list(ctx: RunContext, query: str):
    """Type into a list view's search box and wait for it to settle."""
    try:
        field = ctx.app.by_label_any(sel.DLG_SEARCH_FIELD, timeout=4.0)
        set_text(field, query, description=f"list search {query!r}", retries=1)
    except Exception as exc:  # noqa: BLE001
        log.debug("no search box on this list (%s); reading it unfiltered", exc)

    try:
        wait_for_stable(
            lambda: [cells for _c, cells in grid_items(ctx.app.window)],
            timeout=ctx.settings.short_timeout,
            interval=ctx.settings.poll_interval,
            stable_for=ctx.settings.stable_for,
            description="the list contents",
        )
    except Exception:  # noqa: BLE001
        pass
    return grid_items(ctx.app.window)


def _click_new_entry(ctx: RunContext) -> None:
    """The green '+' at the upper right of a list view."""
    for names in (("New", "Neu"), ("Add", "Hinzufügen"), ("+",)):
        try:
            ctx.app.click_any(names, description="new list entry (+)",
                              control_type="Button", timeout=2.0)
            return
        except Exception:  # noqa: BLE001
            continue
    try:
        icons = icons_near(ctx.app, sel.DLG_SEARCH_FIELD, max_distance=400)
        if icons:
            click(icons[0], description="new list entry (+) beside the search box")
            return
    except Exception:  # noqa: BLE001
        pass
    ctx.stop("could not find the '+' control that adds a new list entry")
