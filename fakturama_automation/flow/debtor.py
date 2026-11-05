"""Stage 2 of the brief: select the Debtor, or create it and select it.

The Order's own address selector is both the existence check *and* the proof
of creation.  Nothing here trusts a Save button: a new debtor only counts as
saved once the still-open Order can find and select it.
"""

from __future__ import annotations

import time
from typing import Any, List, Sequence, Tuple

from ..errors import ControlNotFoundError
from ..logging_setup import get_logger
from ..models import Address
from ..uia import selectors as sel
from ..uia.actions import click, read_value, select_combo, set_text
from ..uia.locators import normalize, rect_of, safe_name
from ..uia.waits import wait_for_stable, wait_until
from .context import RunContext
from .payment_terms import ensure_payment_method, ensure_payment_term
from .widgets import (
    grid_items_or_screen, icons_near, is_screen_row, row_contains_all,
)

log = get_logger("debtor")

STEP = "2-debtor"


def resolve_debtor(ctx: RunContext) -> None:
    """Select an exact existing Debtor, or create one and then select it."""
    selected = try_select_debtor(ctx, allow_missing=True)
    if selected:
        confirm_addresses_populated(ctx)
        ctx.record("debtor.source", "existing record selected from the Order")
        return

    log.info("no exact Debtor match; creating one while the Order stays open")
    create_debtor(ctx)

    # 2.12-2.13  Back to the still-open Order; selection proves the save.
    if not try_select_debtor(ctx, allow_missing=False):
        ctx.stop(
            "the newly created Debtor could not be selected from the Order, so "
            "it cannot be confirmed as saved"
        )
    confirm_addresses_populated(ctx)
    ctx.record("debtor.source", "created, then selected from the Order")


# --------------------------------------------------------------- selection


def try_select_debtor(ctx: RunContext, *, allow_missing: bool) -> bool:
    """2.1-2.3  Search the address selector for an exact match."""
    dialog = open_address_selector(ctx)
    order = ctx.order

    rows = search_and_settle(ctx, dialog, order.company)
    if not rows:
        # Fall back to the contact's name, as the brief permits.
        rows = search_and_settle(ctx, dialog, order.contact.full_name)

    required = [
        order.company,
        order.contact.first_name,
        order.contact.last_name,
        order.billing_address.zip_code,
        order.billing_address.city,
    ]
    exact = [
        (control, cells) for control, cells in rows
        if row_contains_all(cells, required,
                            ocr_tolerant=is_screen_row(control))
    ]

    if len(exact) > 1:
        close_dialog(ctx, dialog, sel.BTN_CANCEL)
        ctx.stop(
            "several Debtors match the extracted company, name, ZIP and city "
            "exactly; a human must choose",
            candidates=[" | ".join(cells) for _c, cells in exact],
        )

    if not exact:
        near_misses = [" | ".join(cells) for _c, cells in rows][:5]
        close_dialog(ctx, dialog, sel.BTN_CANCEL)
        if allow_missing:
            log.info("no exact Debtor row; wanted %r, saw %d candidate row(s): %s",
                     required, len(near_misses), near_misses)
            return False
        ctx.stop(
            "the Debtor could not be found in the Order's address selector",
            searched_for=order.company, rows_seen=near_misses,
        )

    control, cells = exact[0]
    log.info("exact Debtor match: %s", " | ".join(cells))
    select_row(control, description="Debtor row")
    click_dialog_button(ctx, dialog, sel.BTN_OK, description="address selector OK")
    wait_dialog_closed(ctx, sel.DLG_SELECT_ADDRESS)
    ctx.record("debtor.matched_row", " | ".join(cells))
    return True


def open_address_selector(ctx: RunContext):
    """2.1  The upper icon beside Addresses -- never the green '+' below it."""
    # The Order must be the editor in front: while the Debtor or any other
    # master-data editor is active, the Order's Addresses area is not visible
    # to UIA and its icons simply cannot be found.
    from .order import activate_order

    activate_order(ctx)
    icons = icons_near(ctx.app, sel.LBL_ADDRESSES)
    if not icons:
        ctx.stop("no selector icons found beside the Addresses area")
    # icons_near returns visual order; the upper one opens the selector, the
    # lower green '+' would start a new Debtor and skip the existence check.
    click(icons[0], description="existing-contact selector icon (upper)")
    return ctx.app.dialog(sel.DLG_SELECT_ADDRESS, timeout=ctx.settings.timeout)


def search_and_settle(ctx: RunContext, dialog, query: str) -> List[Tuple[Any, List[str]]]:
    """2.2  Type a query and wait for the result list to stop changing."""
    if not query or not query.strip():
        return []

    field = find_search_field(ctx, dialog)
    set_text(field, query, description=f"selector search {query!r}", retries=1)

    wait_for_stable(
        lambda: [cells for _c, cells in grid_items_or_screen(dialog)],
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        stable_for=ctx.settings.stable_for,
        description="the selector result list",
    )
    rows = grid_items_or_screen(dialog, refine=True)
    log.info("search %r returned %d row(s)", query, len(rows))
    return rows


def find_search_field(ctx: RunContext, dialog):
    try:
        return ctx.app.by_label_any(sel.DLG_SEARCH_FIELD, root=dialog, timeout=3.0)
    except Exception:  # noqa: BLE001
        pass
    edits = ctx.app.finder.descendants(control_type="Edit", root=dialog)
    if not edits:
        raise ControlNotFoundError("the selector dialog has no search field")
    return edits[0]


def select_row(control, *, description: str) -> None:
    # Lambdas, not bound attributes: a row read off the screen implements only
    # some of these, and an eager lookup would raise before anything is tried.
    for attempt in (lambda: control.select(), lambda: control.click_input(),
                    lambda: control.invoke()):
        try:
            attempt()
            return
        except Exception:  # noqa: BLE001
            continue
    raise ControlNotFoundError(f"could not select {description}")


def click_dialog_button(ctx: RunContext, dialog, names: Sequence[str], *,
                        description: str) -> None:
    button = ctx.app.find_any(names, control_type="Button", root=dialog, timeout=6.0)
    click(button, description=description)


def close_dialog(ctx: RunContext, dialog, names: Sequence[str]) -> None:
    try:
        click_dialog_button(ctx, dialog, names, description=f"dialog {names[0]}")
        wait_dialog_closed(ctx, sel.DLG_SELECT_ADDRESS)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not close dialog cleanly: %s", exc)


def wait_dialog_closed(ctx: RunContext, titles: Sequence[str]) -> None:
    wait_until(
        lambda: ctx.app.dialog_closed(titles),
        timeout=ctx.settings.short_timeout,
        interval=ctx.settings.poll_interval,
        description=f"dialog {list(titles)} to close",
    )


def confirm_addresses_populated(ctx: RunContext) -> None:
    """2.4 / 2.13  The Order must now show the right invoice & delivery address."""
    order = ctx.order
    expected_tokens = [
        order.billing_address.zip_code,
        order.billing_address.city,
        order.company,
    ]
    text = _addresses_area_text(ctx)
    missing = [t for t in expected_tokens if t and normalize(t) not in normalize(text)]
    if missing:
        ctx.stop(
            "the Order's address block does not show the extracted debtor "
            "details after selection",
            missing=missing, shown=text[:400],
        )
    ctx.record("debtor.addresses_on_order", " ".join(text.split())[:200])


def _addresses_area_text(ctx: RunContext) -> str:
    """Whatever the Order is currently displaying as its addresses.

    Fakturama puts the address block in a read-only multi-line Edit inside a
    tab captioned 'Invoice address' -- the caption is the *tab*, not a static
    label beside a field.  Looking for a label of that name finds only the
    'Addresses' heading, whose own container holds nothing but that heading
    and two icons, so the check would compare the word "Addresses" against the
    customer's details and stop every run.
    """
    from ..uia.actions import read_value
    from ..uia.locators import safe_name

    chunks: List[str] = []
    captions = (
        sel.LBL_INVOICE_ADDRESS + sel.LBL_DELIVERY_ADDRESS + sel.LBL_ADDRESSES
    )
    seen = set()
    for caption in captions:
        for control_type in ("Tab", "Group", "Pane"):
            for container in ctx.app.finder.descendants(
                control_type=control_type, name=caption
            ):
                try:
                    edits = container.descendants(control_type="Edit")
                except Exception:  # noqa: BLE001
                    continue
                for edit in edits:
                    value = read_value(edit)
                    if value and value not in seen:
                        seen.add(value)
                        chunks.append(value)

    if chunks:
        return " ".join(chunks)

    # Fallback: the older label-anchored read, for builds laid out differently.
    for labels in (sel.LBL_INVOICE_ADDRESS, sel.LBL_DELIVERY_ADDRESS,
                   sel.LBL_ADDRESSES):
        for anchor in ctx.app.finder.find_labels(labels[0]):
            try:
                parent = anchor.parent()
                for node in parent.descendants():
                    name = safe_name(node) or read_value(node)
                    if name:
                        chunks.append(name)
            except Exception:  # noqa: BLE001
                continue
        if chunks:
            break
    return " ".join(chunks)


# ---------------------------------------------------------------- creation


def create_debtor(ctx: RunContext) -> None:
    """2.5-2.11  Fill and save a new Debtor, leaving the Order open."""
    app, order = ctx.app, ctx.order

    # 2.10, performed first on purpose: the Debtor's payment dropdown is built
    # when the editor is constructed, so the term of payment has to be in the
    # database before that happens.  See ensure_payment_term().
    ensure_payment_term(ctx)

    app.click_any(sel.NAV_NEW_CONTACT, description="New Contact (left panel)",
                  timeout=ctx.settings.timeout)
    wait_until(
        lambda: app.by_label_any(sel.LBL_COMPANY, timeout=3.0),
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        description="the New Debtor editor",
    )

    # Remember this editor's tab so the payment-method detour, which opens an
    # editor of its own on top, can bring the Debtor back afterwards.
    from .order import _active_editor_tab

    ctx.debtor_tab = _active_editor_tab(ctx)

    # 2.6  Keep the proposed Customer ID; fill identity fields.
    proposed_id = _safe_read(ctx, sel.LBL_CUSTOMER_ID)
    ctx.record("debtor.proposed_customer_id", proposed_id)

    set_text(app.by_label_any(sel.LBL_COMPANY), order.company, description="Company")
    _set_names(ctx)

    # 2.7-2.8  Main address and its roles.
    _fill_main_address(ctx)

    # 2.9  Miscellaneous: alias, 0% discount, Net.
    _fill_miscellaneous(ctx)

    # 2.10  Select the payment method, which ensure_payment_term() has
    # already made sure exists.
    ensure_payment_method(ctx)

    # 2.11  Save once.
    app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save (Debtor)",
                  control_type="Button", timeout=ctx.settings.timeout)
    ctx.note_created("debtors", f"{order.company} / {order.contact.full_name}")
    app.screenshot("2-debtor-saved", "New Debtor saved")


def _safe_read(ctx: RunContext, labels: Sequence[str]) -> str:
    try:
        return read_value(ctx.app.by_label_any(labels, timeout=3.0))
    except Exception:  # noqa: BLE001
        return ""


def _set_names(ctx: RunContext) -> None:
    """First Name and Name sit side by side behind one label."""
    app, contact = ctx.app, ctx.order.contact
    try:
        first = app.by_label_any(sel.LBL_FIRST_AND_LAST, occurrence=0, timeout=4.0)
        last = app.by_label_any(sel.LBL_FIRST_AND_LAST, occurrence=1, timeout=4.0)
    except Exception as exc:  # noqa: BLE001
        ctx.stop("could not locate the First Name / Name fields on the Debtor editor",
                 error=str(exc))
    set_text(first, contact.first_name, description="First Name")
    set_text(last, contact.last_name, description="Name")


def _fill_main_address(ctx: RunContext) -> None:
    app, order = ctx.app, ctx.order
    billing = order.billing_address

    _open_tab(ctx, sel.TAB_ADDRESS)
    _open_tab(ctx, sel.TAB_MAIN_ADDRESS, required=False)

    set_text(app.by_label_any(sel.LBL_STREET), billing.street, description="Street")
    _set_zip_city(ctx, billing)
    _set_optional(ctx, sel.LBL_COUNTRY, billing.country, "Country")
    _set_optional(ctx, sel.LBL_EMAIL, order.contact.email, "E-Mail")
    _set_optional(ctx, sel.LBL_TELEPHONE, order.contact.phone, "Telephone")

    _assign_address_roles(ctx)


def _set_zip_city(ctx: RunContext, address: Address) -> None:
    """'ZIP - City' is one label in front of two adjacent fields."""
    app = ctx.app
    try:
        zip_field = app.by_label_any(sel.LBL_ZIP_CITY, occurrence=0, timeout=4.0)
        city_field = app.by_label_any(sel.LBL_ZIP_CITY, occurrence=1, timeout=4.0)
    except Exception as exc:  # noqa: BLE001
        ctx.stop("could not locate the ZIP / City fields", error=str(exc))
    set_text(zip_field, address.zip_code, description="ZIP")
    set_text(city_field, address.city, description="City")


def _set_optional(ctx: RunContext, labels: Sequence[str], value: str,
                  description: str, *, numeric: bool = False) -> None:
    if not value:
        return
    try:
        set_text(ctx.app.by_label_any(labels, timeout=4.0), value,
                 description=description, numeric=numeric)
    except Exception as exc:  # noqa: BLE001
        log.warning("optional field %s could not be set (%s)", description, exc)
        ctx.extraction_notes.append(f"{description} could not be entered: {exc}")


def _assign_address_roles(ctx: RunContext) -> None:
    """2.8  Invoice role always; Delivery role too when the addresses match."""
    order = ctx.order
    same = not order.delivery_differs
    roles = [sel.ROLE_INVOICE_ADDRESS[0]]
    if same:
        roles.append(sel.ROLE_DELIVERY_ADDRESS[0])

    for role in roles:
        if not _check_role(ctx, role):
            ctx.stop(
                f"could not assign the {role!r} role to the main address",
                roles_wanted=roles,
            )
    ctx.record("debtor.main_address_roles", roles)

    if not same:
        # The brief only spells out the identical-address case. A differing
        # delivery address genuinely needs a second address record, and the
        # Order later verifies both, so this is reported rather than guessed at.
        ctx.extraction_notes.append(
            "billing and delivery addresses differ, so the delivery address "
            "needs its own address record on the Debtor; the brief only "
            "describes the identical case - review this record"
        )
        log.warning("delivery address differs from billing; see run notes")


def _address_type_controls(ctx: RunContext):
    """The 'address type' read-back field and the button that opens its picker.

    The field is not a combo box.  It is a read-back Edit with a small menu
    button at its right-hand edge, and the role checkboxes do not exist in the
    accessibility tree until that button has been clicked.  Treating it as a
    combo and selecting into it falls through to *typing*, which appends:
    a run produced 'invoice addressdelivery addressdelivery address', which
    Fakturama then rejected as an invalid entry.
    """
    label = None
    for caption in sel.LBL_ADDRESS_TYPE:
        hits = ctx.app.finder.find_labels(caption, exact=False)
        if hits:
            label = hits[0]
            break
    if label is None:
        return None, None

    anchor = rect_of(label)
    field = None
    for control in ctx.app.finder.descendants(control_type="Edit"):
        rect = rect_of(control)
        if abs(rect.top - anchor.top) < 25 and rect.left >= anchor.right - 4:
            if field is None or rect.left < rect_of(field).left:
                field = control
    if field is None:
        return None, None

    edge = rect_of(field)
    opener = None
    for control in ctx.app.finder.descendants(control_type="Button"):
        rect = rect_of(control)
        if abs(rect.top - edge.top) < 12 and 0 <= rect.left - edge.right <= 30:
            opener = control
            break
    return field, opener


def _role_checkboxes(ctx: RunContext) -> dict:
    """Open the address-type picker and return its checkboxes by caption."""
    field, opener = _address_type_controls(ctx)
    if field is None:
        return {}

    boxes = {normalize(safe_name(c)): c
             for c in ctx.app.finder.descendants(control_type="CheckBox")
             if safe_name(c)}
    if boxes or opener is None:
        return boxes

    click(opener, description="address type picker")
    time.sleep(0.4)
    return {normalize(safe_name(c)): c
            for c in ctx.app.finder.descendants(control_type="CheckBox")
            if safe_name(c)}


def _check_role(ctx: RunContext, role: str) -> bool:
    """Give the main address one role, leaving any it already has alone.

    2.8 can require *two* roles on one address, so each is a checkbox to be
    ticked rather than a value to be selected -- selecting would replace.
    """
    from ..uia.actions import set_checkbox

    target = normalize(role)
    boxes = _role_checkboxes(ctx)
    control = boxes.get(target)
    if control is None:
        for caption, candidate in boxes.items():
            if target in caption or caption in target:
                control = candidate
                break
    if control is None:
        log.debug("no address-role checkbox matching %r; saw %s",
                  role, list(boxes))
        return False

    try:
        set_checkbox(control, True, description=f"address role {role!r}")
    except Exception as exc:  # noqa: BLE001
        log.debug("could not tick the %r checkbox: %s", role, exc)
        return False

    # Read the field back: it lists the roles the address actually carries.
    field, _opener = _address_type_controls(ctx)
    if field is None:
        return True
    shown = read_value(field)
    if target in normalize(shown):
        log.info("address role %r set (field reads %r)", role, shown)
        return True
    log.debug("ticked %r but the field reads %r", role, shown)
    return False


def _fill_miscellaneous(ctx: RunContext) -> None:
    """2.9  Alias name, Discount 0%, Net or Gross = Net."""
    app, order = ctx.app, ctx.order
    _open_tab(ctx, sel.TAB_MISCELLANEOUS)

    _set_optional(ctx, sel.LBL_ALIAS, order.alias, "Alias name")
    # Compared numerically: the field re-renders '0' as '0%', which is the
    # same value, and a literal text comparison would call that a failure.
    _set_optional(ctx, sel.LBL_DISCOUNT, "0", "Discount", numeric=True)
    try:
        combo = app.by_label_any(sel.LBL_NET_OR_GROSS, timeout=4.0)
        select_combo(combo, sel.OPT_NET[0], description="Net or Gross")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not set Net/Gross on the Debtor (%s)", exc)
        ctx.extraction_notes.append(f"Debtor Net/Gross not set explicitly: {exc}")


def _open_tab(ctx: RunContext, names: Sequence[str], required: bool = True) -> None:
    for control_type in ("TabItem", "Button", "ListItem", "Text"):
        try:
            control = ctx.app.find_any(names, control_type=control_type, timeout=2.0)
        except Exception:  # noqa: BLE001
            continue
        try:
            click(control, description=f"tab {names[0]!r}")
            return
        except Exception:  # noqa: BLE001
            continue
    if required:
        log.warning("tab %r not found; assuming its fields are already visible",
                    names[0])
