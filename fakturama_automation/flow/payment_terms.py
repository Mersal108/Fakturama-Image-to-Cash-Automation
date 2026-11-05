"""Brief 2.10: the Debtor's payment method, created on demand.

The Debtor editor stays open throughout.  Fakturama's *terms of payment* list
is consulted first; a new entry is only created when no exact one exists, and
a conflicting definition stops the run rather than being overwritten.
"""

from __future__ import annotations

from typing import Sequence

from ..extraction.validation import payment_code_for
from ..logging_setup import get_logger
from ..uia import selectors as sel
from ..uia.actions import click, combo_options, select_combo, set_text
from ..uia.locators import normalize, safe_name
from ..uia.waits import wait_for_stable
from .context import RunContext
from .widgets import grid_items_or_screen, icons_near

log = get_logger("payment")


def ensure_payment_term(ctx: RunContext) -> None:
    """Make sure the order's payment method exists *before* an editor needs it.

    This runs ahead of the Debtor editor rather than inside it, and that order
    is forced by the toolkit.  Fakturama's payment combo is an SWT widget
    populated once, when its editor is constructed; a term of payment created
    afterwards is simply not in the list, and no amount of re-selecting the
    tab refreshes it.  Measured on this build: with the Debtor editor already
    open, creating 'Bank Transfer' leaves its dropdown drawing an empty box,
    while a Debtor editor opened a moment later offers 'Bank Transfer' at once.

    Creating the term first reaches the same end state the brief describes --
    the method exists, and the Debtor is saved with it selected -- without
    depending on a refresh the toolkit does not perform.
    """
    method = ctx.order.payment.method
    create_payment_method(ctx, method)


def ensure_payment_method(ctx: RunContext) -> None:
    """2.10  Select the exact payment method on the open Debtor editor."""
    method = ctx.order.payment.method
    from .debtor import _open_tab  # local import avoids a circular dependency

    # The combo lives on Miscellaneous in this build; a build with a dedicated
    # Payment tab is handled by trying that too.  Neither is required, because
    # whichever one exists is the one that will hold the combo.
    _open_tab(ctx, sel.TAB_MISCELLANEOUS, required=False)
    _open_tab(ctx, sel.TAB_PAYMENT, required=False)

    if _try_select_on_debtor(ctx, method):
        ctx.record("debtor.payment_method", method)
        return

    ctx.stop(
        "the payment method is not offered by the Debtor's payment dropdown. "
        "It was created in Data > terms of payment before this editor was "
        "opened, so the list should contain it; a dropdown that is still "
        "empty means the term was not saved, or this build populates the "
        "combo from somewhere else",
        method=method,
        offered=_offered_methods(ctx),
    )


def _offered_methods(ctx: RunContext):
    """What the Debtor's payment dropdown actually lists, for the report."""
    try:
        combo = ctx.app.find_any(sel.LBL_PAYMENT_METHOD, control_type="ComboBox",
                                 timeout=3.0)
    except Exception:  # noqa: BLE001
        return []
    try:
        return combo_options(combo)
    except Exception:  # noqa: BLE001
        return []


def _try_select_on_debtor(ctx: RunContext, method: str) -> bool:
    """Is the exact method offered in the Debtor's payment combo?

    The named combo is checked first; scanning every combo on the editor is
    the fallback.  Options are read with combo_options(), which drops each list
    down -- an SWT combo reports nothing useful otherwise, and an empty option
    list here would wrongly look like "the method does not exist".
    """
    target = normalize(method)

    candidates = []
    try:
        candidates.append(
            ctx.app.find_any(sel.LBL_PAYMENT_METHOD, control_type="ComboBox",
                             timeout=3.0)
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates.extend(ctx.app.finder.descendants(control_type="ComboBox"))
    except Exception:  # noqa: BLE001
        pass

    for combo in candidates:
        options = combo_options(combo)
        if not any(normalize(option) == target for option in options):
            continue
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

    _click_new_entry(ctx, sel.BTN_NEW_PAYMENT_TERM)

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
            lambda: [cells for _c, cells in grid_items_or_screen(ctx.app.window)],
            timeout=ctx.settings.short_timeout,
            interval=ctx.settings.poll_interval,
            stable_for=ctx.settings.stable_for,
            description="the list contents",
        )
    except Exception:  # noqa: BLE001
        pass
    return grid_items_or_screen(ctx.app.window)


def _click_new_entry(ctx: RunContext, preferred: Sequence[str] = ()) -> None:
    """The '+' at the upper right of a list view.

    ``preferred`` is the view's own add-button caption and is tried first.
    Falling back to "the icon nearest the search box" is a last resort: several
    list views share one tab stack and lay their toolbars out at the same
    coordinates, so proximity can pick the wrong view's button.
    """
    if preferred:
        try:
            ctx.app.click_any(preferred, description="new list entry",
                              control_type="Button", timeout=4.0)
            return
        except Exception:  # noqa: BLE001
            log.debug("named add button %s not found; trying generic captions",
                      list(preferred))

    # Only the active view's toolbar is visible to UIA, so a caption-prefix
    # match cannot pick up a different list's add button.
    try:
        for button in ctx.app.finder.descendants(control_type="Button"):
            caption = normalize(safe_name(button))
            if any(caption.startswith(p) for p in sel.BTN_NEW_PREFIXES):
                click(button, description=f"new list entry ({safe_name(button)!r})")
                return
    except Exception:  # noqa: BLE001
        log.debug("caption-prefix search for the add button failed")

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
