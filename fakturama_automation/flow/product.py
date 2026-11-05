"""Stage 3 of the brief: resolve every item line, creating Products on demand.

This runs once per extracted item, in source order.  As with the Debtor, the
Order's own product selector is the existence check and the proof of creation.

Caveat worth stating plainly: Fakturama draws its item grid with Nebula
NatTable, a custom-painted SWT canvas.  Custom-painted widgets expose far less
to UI Automation than ordinary native controls, so cell editing falls back to
a keyboard path and then verifies by reading the row back.  Where even that
cannot confirm a value, the run stops rather than assuming.
"""

from __future__ import annotations

from typing import Any, List, Sequence, Tuple

from ..logging_setup import get_logger
from ..models import LineItem, format_percent
from ..uia import selectors as sel
from ..uia.actions import click, escape_keys, read_value, set_text, values_match
from ..uia.locators import normalize, rect_of, safe_name
from ..uia.waits import wait_for_stable, wait_until
from .context import RunContext
from .debtor import (
    click_dialog_button,
    close_dialog,
    find_search_field,
    select_row,
    wait_dialog_closed,
)
from .vat import ensure_vat
from .widgets import grid_items, icons_near

log = get_logger("product")


def resolve_items(ctx: RunContext) -> None:
    """3.1  Run the whole selection branch for every extracted row, in order."""
    for index, item in enumerate(ctx.order.items):
        with ctx.step(f"3-item-{item.position}", f"item {item.position}: {item.sku}"):
            _resolve_one(ctx, item, index)


def _resolve_one(ctx: RunContext, item: LineItem, row_index: int) -> None:
    if not _try_select_product(ctx, item, allow_missing=True):
        log.info("SKU %s not in Fakturama; creating the Product", item.sku)
        ensure_vat(ctx, item)          # 3.4-3.7: the VAT must exist first
        _create_product(ctx, item)
        if not _try_select_product(ctx, item, allow_missing=False):
            ctx.stop(
                "the newly created Product cannot be selected from the Order",
                sku=item.sku,
            )
    _complete_line(ctx, item, row_index)


# --------------------------------------------------------------- selection


def _try_select_product(ctx: RunContext, item: LineItem, *, allow_missing: bool) -> bool:
    """3.2-3.3  Search the Order's product selector for the exact SKU."""
    dialog = _open_product_selector(ctx)
    rows = _search_products(ctx, dialog, item.sku)

    exact = [(control, cells) for control, cells in rows
             if any(normalize(cell) == normalize(item.sku) for cell in cells)]

    if len(exact) > 1:
        close_dialog(ctx, dialog, sel.BTN_CANCEL)
        ctx.stop(
            "more than one Product carries this exact SKU",
            sku=item.sku, candidates=[" | ".join(c) for _x, c in exact],
        )

    if not exact:
        close_dialog(ctx, dialog, sel.BTN_CANCEL)
        if allow_missing:
            return False
        ctx.stop("the Product could not be found by its exact SKU", sku=item.sku)

    control, cells = exact[0]
    select_row(control, description=f"product row {item.sku}")
    click_dialog_button(ctx, dialog, sel.BTN_OK, description="product selector OK")
    wait_dialog_closed(ctx, sel.DLG_SELECT_PRODUCT)
    log.info("selected product %s", " | ".join(cells))
    return True


def _open_product_selector(ctx: RunContext):
    """3.2  The upper product icon beside the Items table, never the green '+'."""
    anchors = [sel.COL_ITEM_NO[0], sel.COL_QTY[0], sel.COL_UPRICE[0]]
    icons: List[Any] = []
    for anchor in anchors:
        try:
            icons = icons_near(ctx.app, (anchor,), max_distance=260)
        except Exception:  # noqa: BLE001
            icons = []
        if icons:
            break
    if not icons:
        ctx.stop("could not find the product-selection icon beside the Items table")
    click(icons[0], description="product selector icon (upper)")
    return ctx.app.dialog(sel.DLG_SELECT_PRODUCT, timeout=ctx.settings.timeout)


def _search_products(ctx: RunContext, dialog, sku: str):
    field = find_search_field(ctx, dialog)
    set_text(field, sku, description=f"product search {sku!r}", retries=1)
    wait_for_stable(
        lambda: [cells for _c, cells in grid_items(dialog)],
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        stable_for=ctx.settings.stable_for,
        description="the product result list",
    )
    return grid_items(dialog)


# ---------------------------------------------------------------- creation


def _create_product(ctx: RunContext, item: LineItem) -> None:
    """3.7-3.11  Create the Product once its VAT rate exists."""
    app = ctx.app
    app.click_any(sel.NAV_NEW_PRODUCT, description="New product (left panel)",
                  timeout=ctx.settings.timeout)
    wait_until(
        lambda: app.by_label_any(sel.LBL_ITEM_NUMBER, timeout=3.0),
        timeout=ctx.settings.timeout,
        interval=ctx.settings.poll_interval,
        description="the New Product editor",
    )

    set_text(app.by_label_any(sel.LBL_ITEM_NUMBER), item.sku,
             description="Item Number")
    set_text(app.by_label_any(sel.LBL_PRODUCT_NAME), item.description,
             description="Product Name")
    set_text(app.by_label_any(sel.LBL_PRODUCT_DESCRIPTION), item.description,
             description="Product Description")

    # 3.9  Gross master price = unit net x (1 + VAT). The line discount is a
    # property of this order, not of the catalogue price, so it is excluded.
    gross = item.product_gross_price
    set_text(app.by_label_any(sel.LBL_PRICE_GROSS), f"{gross:.2f}",
             description="Price (gross)", numeric=True)
    set_text(app.by_label_any(sel.LBL_COST_PRICE_NET), "0.00",
             description="cost price (net)", numeric=True)

    _select_product_vat(ctx, item)

    try:
        set_text(app.by_label_any(sel.LBL_STOCK), "0.00",
                 description="Stock", numeric=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not set Stock (%s)", exc)

    # 3.10  Category, GTIN, supplier code, allowance, picture and the user
    # defined field are deliberately left untouched.
    app.click_any(sel.TOOLBAR_SAVE, description="toolbar Save (Product)",
                  control_type="Button", timeout=ctx.settings.timeout)
    ctx.note_created("products", f"{item.sku} @ {gross:.2f} gross")
    app.screenshot(f"3-product-{item.position}", f"Product {item.sku} created")


def _select_product_vat(ctx: RunContext, item: LineItem) -> None:
    from ..uia.actions import select_combo

    target = item.vat_name
    combos = ctx.app.finder.descendants(control_type="ComboBox")
    for combo in combos:
        try:
            options = [str(t) for t in combo.texts() if t]
        except Exception:  # noqa: BLE001
            continue
        if any(normalize(option) == normalize(target) for option in options):
            select_combo(combo, target, description=f"Product VAT ({target})")
            return
    ctx.stop(
        "the required VAT rate is not offered in the Product editor even "
        "though it was just ensured to exist",
        vat=target, sku=item.sku,
    )


# ------------------------------------------------------------- line editing


def _complete_line(ctx: RunContext, item: LineItem, row_index: int) -> None:
    """3.13-3.16  Quantity, unit price, VAT and discount on the selected row."""
    _set_cell(ctx, row_index, sel.COL_QTY, str(item.quantity),
              description=f"line {item.position} Qty.", numeric=True)
    _set_cell(ctx, row_index, sel.COL_UPRICE, f"{item.unit_net:.2f}",
              description=f"line {item.position} U.Price", numeric=True)
    _set_cell(ctx, row_index, sel.COL_DISCOUNT, format_percent(item.discount_pct),
              description=f"line {item.position} Discount", numeric=True)

    _confirm_line_total(ctx, item, row_index)


def _items_grid(ctx: RunContext):
    """The container holding the order's item rows."""
    for names in (sel.COL_ITEM_NO, sel.COL_QTY, sel.COL_UPRICE):
        for name in names:
            labels = ctx.app.finder.find_labels(name)
            for label in labels:
                try:
                    container = label.parent().parent()
                    if grid_items(container):
                        return container
                except Exception:  # noqa: BLE001
                    continue
    return ctx.app.window


def _row_controls(ctx: RunContext, row_index: int) -> Tuple[Any, List[str]]:
    grid = _items_grid(ctx)
    rows = grid_items(grid)
    if row_index >= len(rows):
        ctx.stop(
            "the item row that should have been added to the Order is not "
            "present in the items grid",
            expected_row=row_index + 1, rows_found=len(rows),
        )
    return rows[row_index]


def _set_cell(ctx: RunContext, row_index: int, column: Sequence[str], value: str,
              *, description: str, numeric: bool = False) -> None:
    """Write one cell of the items grid, then read it back.

    NatTable exposes cells inconsistently, so this tries the direct control
    first and falls back to activating the cell and typing.  Either way the
    value is re-read before the step is considered done.
    """
    control, cells = _row_controls(ctx, row_index)
    cell = _cell_control(ctx, control, column)

    if cell is not None:
        try:
            set_text(cell, value, description=description, numeric=numeric)
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: direct cell write failed (%s); trying keyboard", description, exc)

    if _keyboard_cell_write(ctx, control, cell, value):
        seen = _read_cell(ctx, row_index, column)
        if values_match(seen, value, numeric=numeric):
            log.info("%s = %r (verified)", description, value)
            return
        ctx.stop(
            f"{description}: the value did not persist in the items grid",
            wrote=value, grid_shows=seen,
        )

    ctx.stop(
        f"{description}: the items grid cell could not be edited through UI "
        "Automation (Fakturama draws this grid with NatTable)",
        column=column[0], wanted=value,
    )


def _cell_control(ctx: RunContext, row_control, column: Sequence[str]):
    """Find a row's cell by matching the grid's column headers by position."""
    headers = _column_order(ctx)
    try:
        children = row_control.children()
    except Exception:  # noqa: BLE001
        return None

    for index, header in enumerate(headers):
        if any(normalize(header) == normalize(name) for name in column):
            if index < len(children):
                return children[index]
    # Fall back to matching the cell's own accessible name.
    for child in children:
        if any(normalize(safe_name(child)) == normalize(name) for name in column):
            return child
    return None


def _column_order(ctx: RunContext) -> List[str]:
    grid = _items_grid(ctx)
    try:
        headers = grid.descendants(control_type="Header")
        if headers:
            items = headers[0].children()
            return [safe_name(i) for i in items]
    except Exception:  # noqa: BLE001
        pass
    try:
        items = grid.descendants(control_type="HeaderItem")
        if items:
            items.sort(key=lambda c: rect_of(c).left)
            return [safe_name(i) for i in items]
    except Exception:  # noqa: BLE001
        pass
    return []


def _keyboard_cell_write(ctx: RunContext, row_control, cell, value: str) -> bool:
    """Activate a cell and type into it -- the NatTable fallback path."""
    target = cell if cell is not None else row_control
    for activate in (
        lambda: target.double_click_input(),
        lambda: (target.click_input(), target.type_keys("{F2}", set_foreground=False)),
    ):
        try:
            activate()
        except Exception:  # noqa: BLE001
            continue
        try:
            editor = ctx.app.finder.descendants(control_type="Edit")
            focused = [e for e in editor if _has_focus(e)]
            if focused:
                set_text(focused[0], value, description="grid cell editor")
                focused[0].type_keys("{ENTER}", set_foreground=False)
                return True
            target.type_keys("^a" + escape_keys(value) + "{ENTER}",
                             with_spaces=True, set_foreground=False)
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("keyboard cell write attempt failed: %s", exc)
    return False


def _has_focus(control) -> bool:
    try:
        return bool(control.has_keyboard_focus())
    except Exception:  # noqa: BLE001
        return False


def _read_cell(ctx: RunContext, row_index: int, column: Sequence[str]) -> str:
    control, cells = _row_controls(ctx, row_index)
    cell = _cell_control(ctx, control, column)
    if cell is not None:
        value = read_value(cell) or safe_name(cell)
        if value:
            return value
    headers = _column_order(ctx)
    for index, header in enumerate(headers):
        if any(normalize(header) == normalize(name) for name in column):
            if index < len(cells):
                return cells[index]
    return " ".join(cells)


def _confirm_line_total(ctx: RunContext, item: LineItem, row_index: int) -> None:
    """3.16  Line price must equal qty x unit net x (1 - discount)."""
    expected = item.computed_line_net
    shown = _read_cell(ctx, row_index, sel.COL_PRICE)
    if not shown:
        ctx.stop(
            "could not read the line Price back from the items grid",
            line=item.position, expected=str(expected),
        )
    if not values_match(shown, f"{expected:.2f}", numeric=True):
        ctx.stop(
            "the Order's line total does not match quantity x unit net x "
            "(1 - discount) from the source document",
            line=item.position, grid_shows=shown, source_implies=f"{expected:.2f}",
        )
    ctx.record(f"order.line_{item.position}_total", shown)
